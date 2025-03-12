# main.py
import os
import json
import requests
import sys
import re
import time
from urllib.parse import urlparse
from dotenv import load_dotenv
from firecrawl import FirecrawlApp
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image, ListFlowable, ListItem
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfgen import canvas
from reportlab.platypus.flowables import Flowable
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_CENTER, TA_RIGHT

"""
This script:
 1) Reads a research question from user input (or from sys.argv)
 2) Calls Perplexity's API (non-stream) using a 'research' model from .env
 3) Parses the returned citations
 4) For each citation, intelligently crawls the site with Firecrawl, storing the raw JSON
 5) Calls Perplexity again to format that text nicely in Markdown
 6) Generates a comprehensive PDF report with title page, TOC, executive summary, and all content
 7) Stores the raw responses, cleaned Markdown, and final PDF report in timestamped subfolders

Environment variables in .env:
  - PERPLEXITY_API_KEY
  - FIRECRAWL_API_KEY
  - PERPLEXITY_RESEARCH_MODEL
  - PERPLEXITY_CLEANUP_MODEL
"""

# ANSI color codes for better terminal output
class Colors:
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    MAGENTA = '\033[95m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

# Load environment variables
load_dotenv()
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
PERPLEXITY_RESEARCH_MODEL = os.getenv("PERPLEXITY_RESEARCH_MODEL")
PERPLEXITY_CLEANUP_MODEL = os.getenv("PERPLEXITY_CLEANUP_MODEL")

# Initialize Firecrawl
firecrawl_client = FirecrawlApp(api_key=FIRECRAWL_API_KEY)

def create_run_subfolders(user_query):
    """
    Create timestamped subfolders for the current run.
    Returns the subfolder name used for all directories.
    """
    # Create a safe, unique folder name based on timestamp and query
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    
    # Sanitize query for folder name use
    safe_query = re.sub(r'[^\w\s-]', '', user_query)
    safe_query = re.sub(r'[-\s]+', '_', safe_query).strip('-_')
    # Limit query length in folder name
    safe_query = safe_query[:30] if len(safe_query) > 30 else safe_query
    
    # Create final folder name
    run_folder = f"{timestamp}_{safe_query}"
    
    # Ensure main directories exist
    for main_dir in ["response", "markdown", "reports"]:
        os.makedirs(main_dir, exist_ok=True)
        # Create the run-specific subfolder
        run_path = os.path.join(main_dir, run_folder)
        os.makedirs(run_path, exist_ok=True)
        print(f"{Colors.CYAN}Created output directory: {run_path}{Colors.RESET}")
    
    return run_folder

def clean_thinking_sections(content):
    """
    Remove any <think>...</think> sections from the content.
    """
    if not content:
        return content
    
    # Use regex to remove the <think> sections
    cleaned_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    return cleaned_content

def query_perplexity(prompt, model, system_prompt="Be concise.", is_research=False):
    """
    Non-streaming call to Perplexity. 
    Returns JSON response.
    """
    if not PERPLEXITY_API_KEY:
        raise ValueError("Missing Perplexity API key.")
    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 4000,
        "temperature": 0.2,
        "top_p": 0.9,
        "search_domain_filter": None,
        "return_images": False,
        "return_related_questions": False,
        "search_recency_filter": "month",
        "top_k": 0,
        "stream": False,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "response_format": None
    }
    
    # Use a much longer timeout for research calls
    timeout_duration = 600 if is_research else 120  # 10 minutes for research, 2 minutes for cleanup
    
    print(f"{Colors.YELLOW}Calling Perplexity API with model: {model}" + 
          (f" {Colors.BOLD}(this may take up to 10 minutes...){Colors.RESET}" if is_research else f"{Colors.RESET}"))
    
    response = requests.post(url, json=payload, headers=headers, timeout=timeout_duration)
    if response.status_code != 200:
        raise RuntimeError(f"Perplexity API call failed: {response.text}")
    return response.json()

def generate_executive_summary(query, research_content, model):
    """
    Generate an executive summary of the research using Perplexity.
    """
    print(f"{Colors.BLUE}Generating executive summary...{Colors.RESET}")
    
    # Truncate research_content if it's too long
    max_content_length = 15000  # Limit to roughly 15k characters
    if len(research_content) > max_content_length:
        truncated_content = research_content[:max_content_length] + "...[content truncated]"
    else:
        truncated_content = research_content
    
    prompt = f"""
Please create a concise executive summary (500-750 words) for the following research on:
"{query}"

The summary should:
1. Highlight key findings and insights
2. Use professional, business-friendly language
3. Be well-structured with clear sections
4. Include important facts and statistics from the research

Research content:
{truncated_content}
"""
    
    response = query_perplexity(
        prompt=prompt,
        model=model,
        system_prompt="You are a professional research analyst creating an executive summary. Be clear, concise, and analytical."
    )
    
    summary = ""
    if response.get("choices") and len(response["choices"]) > 0:
        summary = response["choices"][0]["message"].get("content", "")
    
    return summary

def intelligent_scrape(url, research_query):
    """
    More intelligently scrape a URL using Firecrawl.
    For complex sites, uses site mapping first to find relevant pages.
    """
    print(f"{Colors.CYAN}Analyzing URL: {url}{Colors.RESET}")
    
    # Check for social media and video sites that are known to be blocked
    social_media_domains = ['tiktok.com', 'youtube.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com']
    parsed_url = urlparse(url)
    
    if any(domain in parsed_url.netloc for domain in social_media_domains):
        print(f"{Colors.YELLOW}Warning: {url} is a social media or video site which may require special Firecrawl access.{Colors.RESET}")
    
    try:
        # Simple sites: Direct scrape with markdown format
        result = firecrawl_client.scrape_url(url, params={"formats": ["markdown", "html"]})
        
        # Check if we got useful content
        if result.get("markdown") and len(result["markdown"]) > 300:
            print(f"{Colors.GREEN}Successfully scraped direct content.{Colors.RESET}")
            return result
        
        # If no good content, try mapping the site for complex sites
        print(f"{Colors.YELLOW}Direct scrape yielded limited content. Attempting site mapping...{Colors.RESET}")
        
        # Extract the base domain for mapping
        base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        # Map the site to get relevant URLs
        map_result = firecrawl_client.map_url(base_domain, params={"search": research_query[:30]})
        
        # Extract URLs from the map result
        mapped_urls = []
        if isinstance(map_result, dict) and "urls" in map_result:
            mapped_urls = map_result["urls"]
        elif isinstance(map_result, list):
            mapped_urls = map_result
            
        # If mapping found URLs, scrape the first 2-3 most relevant
        if mapped_urls and len(mapped_urls) > 0:
            print(f"{Colors.GREEN}Site mapping found {len(mapped_urls)} potential URLs.{Colors.RESET}")
            
            # Prioritize the original URL if it's in the mapped results
            if url in mapped_urls:
                mapped_urls.remove(url)
                mapped_urls.insert(0, url)
            
            # Only take the first 3 URLs to avoid excessive API calls
            urls_to_scrape = mapped_urls[:3]
            
            # Combine results from multiple pages
            combined_result = {"markdown": "", "html": ""}
            
            for mapped_url in urls_to_scrape:
                print(f"{Colors.YELLOW}Scraping mapped URL: {mapped_url}{Colors.RESET}")
                try:
                    page_result = firecrawl_client.scrape_url(mapped_url, params={"formats": ["markdown", "html"]})
                    if page_result.get("markdown"):
                        combined_result["markdown"] += f"\n\n## Content from {mapped_url}\n\n" + page_result["markdown"]
                    if page_result.get("html"):
                        combined_result["html"] += f"\n\n<!-- Content from {mapped_url} -->\n\n" + page_result["html"]
                except Exception as e:
                    print(f"{Colors.RED}Error scraping mapped URL {mapped_url}: {str(e)}{Colors.RESET}")
            
            if combined_result["markdown"]:
                print(f"{Colors.GREEN}Successfully scraped content from mapped URLs.{Colors.RESET}")
                return combined_result
        
        # If we still don't have good content, return what we have
        print(f"{Colors.YELLOW}Limited content available from this URL.{Colors.RESET}")
        return result
        
    except Exception as e:
        error_msg = str(e)
        
        # Special handling for common Firecrawl errors
        if "403" in error_msg and "no longer supported" in error_msg:
            print(f"{Colors.RED}This website requires special access with Firecrawl: {error_msg}{Colors.RESET}")
            
            # For social media sites, create a special message
            if any(domain in parsed_url.netloc for domain in social_media_domains):
                return {
                    "markdown": f"# Content from {url} (Social Media/Video Site)\n\n"
                               f"This citation is from a social media or video platform that requires special access with Firecrawl.\n\n"
                               f"To view this content, please visit the URL directly: [{url}]({url})\n\n"
                               f"**Note**: Social media and video content often requires special permissions to scrape programmatically."
                }
        elif "400" in error_msg:
            print(f"{Colors.RED}Invalid request to Firecrawl: {error_msg}{Colors.RESET}")
        else:
            print(f"{Colors.RED}Error scraping URL: {error_msg}{Colors.RESET}")
        
        raise

class PDFReport:
    """
    Class to handle PDF report generation with ReportLab.
    """
    def __init__(self, filename, title):
        self.filename = filename
        self.title = title
        self.doc = SimpleDocTemplate(
            filename,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )
        self.styles = getSampleStyleSheet()
        self.elements = []
        
        # Modify existing styles rather than adding new ones with same names
        self.styles['Title'].fontSize = 24
        self.styles['Title'].spaceAfter = 36
        self.styles['Title'].textColor = colors.darkblue
        
        self.styles['Heading1'].fontSize = 18
        self.styles['Heading1'].spaceBefore = 12
        self.styles['Heading1'].spaceAfter = 6
        self.styles['Heading1'].textColor = colors.darkblue
        
        self.styles['Heading2'].fontSize = 16
        self.styles['Heading2'].spaceBefore = 10
        self.styles['Heading2'].spaceAfter = 4
        self.styles['Heading2'].textColor = colors.darkblue
        
        self.styles['Heading3'].fontSize = 14
        self.styles['Heading3'].spaceBefore = 8
        self.styles['Heading3'].spaceAfter = 4
        self.styles['Heading3'].textColor = colors.darkblue
        
        self.styles['Normal'].fontSize = 11
        self.styles['Normal'].spaceBefore = 4
        self.styles['Normal'].spaceAfter = 4
        self.styles['Normal'].leading = 14
        
        # Add new style that doesn't conflict
        self.styles.add(ParagraphStyle(
            name='Citation',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.darkblue,
            spaceBefore=6,
            spaceAfter=10,
        ))

        # Create a table of contents
        self.toc = TableOfContents()
        self.toc.levelStyles = [
            ParagraphStyle(fontName='Helvetica-Bold', fontSize=14, name='TOCHeading1', leftIndent=0, firstLineIndent=0),
            ParagraphStyle(fontSize=12, name='TOCHeading2', leftIndent=20, firstLineIndent=0),
            ParagraphStyle(fontSize=10, name='TOCHeading3', leftIndent=40, firstLineIndent=0),
        ]
        
    def add_title_page(self):
        """Add a title page to the report."""
        # Add title
        self.elements.append(Paragraph(f"<b>Research Report</b>", self.styles['Title']))
        self.elements.append(Spacer(1, 0.5*inch))
        
        # Add subtitle with research query
        self.elements.append(Paragraph(f"<i>{self.title}</i>", self.styles['Heading2']))
        self.elements.append(Spacer(1, 2*inch))
        
        # Add date
        current_date = time.strftime("%B %d, %Y", time.localtime())
        self.elements.append(Paragraph(f"Generated on: {current_date}", self.styles['Normal']))
        self.elements.append(Spacer(1, 1*inch))
        
        # Add attribution
        self.elements.append(Paragraph("Generated using Perplexity API and Firecrawl", self.styles['Normal']))
        
        # Add page break after title page
        self.elements.append(PageBreak())
    
    def add_toc(self):
        """Add table of contents section."""
        self.elements.append(Paragraph("Table of Contents", self.styles['Heading1']))
        self.elements.append(Spacer(1, 0.2*inch))
        self.elements.append(self.toc)
        self.elements.append(PageBreak())
    
    def add_section(self, title, content, level=1):
        """Add a section with title and content."""
        # Add bookmark for TOC
        bookmark_name = f"section_{len(self.elements)}"
        
        if level == 1:
            self.elements.append(Paragraph(title, self.styles['Heading1']))
        elif level == 2:
            self.elements.append(Paragraph(title, self.styles['Heading2']))
        else:
            self.elements.append(Paragraph(title, self.styles['Heading3']))
        
        # Convert markdown headers to styled paragraphs
        paragraphs = content.split('\n')
        
        current_list = []
        in_list = False
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                if in_list and current_list:
                    # Render the accumulated list
                    list_items = [ListItem(Paragraph(item, self.styles['Normal'])) for item in current_list]
                    self.elements.append(ListFlowable(list_items, bulletType='bullet'))
                    current_list = []
                    in_list = False
                continue
                
            # Check for markdown headers
            if para.startswith('# '):
                if in_list and current_list:
                    list_items = [ListItem(Paragraph(item, self.styles['Normal'])) for item in current_list]
                    self.elements.append(ListFlowable(list_items, bulletType='bullet'))
                    current_list = []
                    in_list = False
                self.elements.append(Paragraph(para[2:], self.styles['Heading1']))
            elif para.startswith('## '):
                if in_list and current_list:
                    list_items = [ListItem(Paragraph(item, self.styles['Normal'])) for item in current_list]
                    self.elements.append(ListFlowable(list_items, bulletType='bullet'))
                    current_list = []
                    in_list = False
                self.elements.append(Paragraph(para[3:], self.styles['Heading2']))
            elif para.startswith('### '):
                if in_list and current_list:
                    list_items = [ListItem(Paragraph(item, self.styles['Normal'])) for item in current_list]
                    self.elements.append(ListFlowable(list_items, bulletType='bullet'))
                    current_list = []
                    in_list = False
                self.elements.append(Paragraph(para[4:], self.styles['Heading3']))
            # Check for list items
            elif para.startswith('- ') or para.startswith('* '):
                in_list = True
                current_list.append(para[2:])
            else:
                if in_list and current_list:
                    list_items = [ListItem(Paragraph(item, self.styles['Normal'])) for item in current_list]
                    self.elements.append(ListFlowable(list_items, bulletType='bullet'))
                    current_list = []
                    in_list = False
                # Regular paragraph
                self.elements.append(Paragraph(para, self.styles['Normal']))
                self.elements.append(Spacer(1, 0.1*inch))
        
        # Handle any remaining list items
        if in_list and current_list:
            list_items = [ListItem(Paragraph(item, self.styles['Normal'])) for item in current_list]
            self.elements.append(ListFlowable(list_items, bulletType='bullet'))
        
        self.elements.append(Spacer(1, 0.2*inch))
    
    def add_citation_section(self, number, url, content):
        """Add a citation section with its content."""
        self.elements.append(PageBreak())
        header = f"Citation {number}: {url}"
        self.elements.append(Paragraph(header, self.styles['Heading1']))
        self.elements.append(Paragraph(f"<link href='{url}'>{url}</link>", self.styles['Citation']))
        self.elements.append(Spacer(1, 0.1*inch))
        
        # Parse the content similar to add_section
        self.add_section("", content, level=3)
    
    def build(self):
        """Build the final PDF document."""
        try:
            print(f"Building PDF with {len(self.elements)} elements...")
            self.doc.multiBuild(self.elements)
            return True
        except Exception as e:
            print(f"Error building PDF: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

def main():
    # Get research query from user or command line
    user_query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    if not user_query:
        user_query = input(f"{Colors.BLUE}Enter your research query: {Colors.RESET}").strip()
    
    if not user_query:
        print(f"{Colors.RED}No research query provided. Exiting.{Colors.RESET}")
        return
    
    # Create run-specific subfolders
    run_folder = create_run_subfolders(user_query)
    
    # STEP 1: Call Perplexity with 'research' model for the initial research
    print(f"{Colors.CYAN}Researching: '{user_query}'{Colors.RESET}")
    try:
        research_response = query_perplexity(
            prompt=user_query,
            model=PERPLEXITY_RESEARCH_MODEL,
            system_prompt="Perform thorough research on the user's query.",
            is_research=True
        )
        
        # Dump the main research response JSON to file
        research_filename = os.path.join("response", run_folder, "research_response.json")
        with open(research_filename, "w", encoding="utf-8") as f:
            json.dump(research_response, f, indent=2)
        print(f"{Colors.GREEN}Saved main Perplexity research response to: {research_filename}{Colors.RESET}")
        
        # STEP 2: Extract citations from the root level of the response
        citations = research_response.get("citations", [])
        print(f"{Colors.BOLD}{Colors.CYAN}Found {len(citations)} citation(s).{Colors.RESET}")

        # Get the main content and clean it of thinking sections
        main_content = ""
        if research_response.get("choices") and len(research_response["choices"]) > 0:
            raw_content = research_response["choices"][0]["message"].get("content", "")
            main_content = clean_thinking_sections(raw_content)
        
        # Save the cleaned research summary
        summary_md_path = os.path.join("markdown", run_folder, "research_summary.md")
        with open(summary_md_path, "w", encoding="utf-8") as f:
            f.write(f"# Research Summary: {user_query}\n\n")
            f.write(main_content)
            f.write("\n\n## Citation Links\n\n")
            for i, url in enumerate(citations, 1):
                f.write(f"{i}. [{url}]({url})\n")
        print(f"{Colors.GREEN}Saved research summary to: {summary_md_path}{Colors.RESET}")

        # Generate executive summary
        exec_summary = generate_executive_summary(user_query, main_content, PERPLEXITY_CLEANUP_MODEL)
        
        # Save the executive summary separately
        exec_summary_path = os.path.join("markdown", run_folder, "executive_summary.md")
        with open(exec_summary_path, "w", encoding="utf-8") as f:
            f.write(f"# Executive Summary: {user_query}\n\n")
            f.write(exec_summary)
        print(f"{Colors.GREEN}Saved executive summary to: {exec_summary_path}{Colors.RESET}")

        # STEP 3: Process each citation
        processed_count = 0
        citation_contents = []
        
        for i, citation_url in enumerate(citations, start=1):
            print(f"\n{Colors.BOLD}{Colors.BLUE}[{i}/{len(citations)}] Processing citation: {citation_url}{Colors.RESET}")
            try:
                # Intelligently scrape the URL
                result = intelligent_scrape(citation_url, user_query)
                
                # Save the raw Firecrawl response
                raw_json_path = os.path.join("response", run_folder, f"firecrawl_{i}.json")
                with open(raw_json_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
                print(f"{Colors.GREEN}Saved Firecrawl response to: {raw_json_path}{Colors.RESET}")
                
                # Clean the response with Perplexity
                content_for_cleanup = result.get("markdown") or ""
                if not content_for_cleanup:
                    print(f"{Colors.YELLOW}No textual content found for cleanup. Skipping.{Colors.RESET}")
                    citation_contents.append({"url": citation_url, "content": "No content available."})
                    continue

                print(f"{Colors.YELLOW}Cleaning up content with Perplexity...{Colors.RESET}")
                # Limit content size to avoid token limits
                truncated_content = content_for_cleanup[:30000] if len(content_for_cleanup) > 30000 else content_for_cleanup
                
                cleanup_prompt = f"""
Rewrite the following content more cleanly as Markdown.
Make it structured, neat, and well-formatted.
This is from the URL: {citation_url}

{truncated_content}
"""
                cleanup_response = query_perplexity(
                    prompt=cleanup_prompt,
                    model=PERPLEXITY_CLEANUP_MODEL,
                    system_prompt="You are a Markdown formatter. Return only well-structured Markdown with clear headings, proper lists, and good organization of information."
                )
                
                # Extract the final text from cleanup
                cleaned_content = ""
                if cleanup_response.get("choices") and len(cleanup_response["choices"]) > 0:
                    cleaned_content = cleanup_response["choices"][0]["message"].get("content", "")
                else:
                    cleaned_content = "Error: Failed to get cleaned content from Perplexity."
                
                # Save the cleaned markdown
                md_filename = os.path.join("markdown", run_folder, f"citation_{i}.md")
                with open(md_filename, "w", encoding="utf-8") as f:
                    f.write(f"# Citation {i}: {citation_url}\n\n")
                    f.write(cleaned_content)
                print(f"{Colors.GREEN}Saved cleaned Markdown to: {md_filename}{Colors.RESET}")
                
                # Store for PDF generation
                citation_contents.append({"url": citation_url, "content": cleaned_content})
                processed_count += 1
            
            except Exception as e:
                error_msg = str(e)
                print(f"{Colors.RED}Error processing citation #{i}: {str(e)}{Colors.RESET}")
                
                # Create a special error message for the citation
                error_content = f"# Error Processing Citation\n\n**URL**: {citation_url}\n\n"
                
                # Add more specific info for common error types
                if "403" in error_msg and ("no longer supported" in error_msg or "requires special access" in error_msg):
                    parsed_url = urlparse(citation_url)
                    if any(domain in parsed_url.netloc for domain in ['tiktok.com', 'youtube.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com']):
                        error_content += "**This is a social media or video site that requires special access with Firecrawl.**\n\n"
                        error_content += f"To view this content, please visit the URL directly: [{citation_url}]({citation_url})\n\n"
                    else:
                        error_content += f"**This website requires special access with Firecrawl**\n\n"
                        error_content += f"Error details: {error_msg}\n\n"
                else:
                    error_content += f"**Error details**: {error_msg}\n\n"
                
                citation_contents.append({"url": citation_url, "content": error_content})
                continue
        
        # STEP 4: Generate PDF Report
        print(f"\n{Colors.MAGENTA}Generating comprehensive PDF report...{Colors.RESET}")
        
        # Use the run folder name for the PDF filename
        reports_dir = os.path.join("reports", run_folder)
        os.makedirs(reports_dir, exist_ok=True)  # Ensure reports subfolder exists
        pdf_filename = os.path.join(reports_dir, "research_report.pdf")
        
        try:
            # Check if reportlab is properly installed
            try:
                from reportlab.pdfgen import canvas
                print(f"{Colors.GREEN}ReportLab is properly installed.{Colors.RESET}")
            except ImportError:
                print(f"{Colors.RED}ReportLab is not installed correctly. Installing now...{Colors.RESET}")
                os.system("pip install reportlab")
                from reportlab.pdfgen import canvas
            
            # Create the PDF report
            print(f"{Colors.YELLOW}Creating PDF document at: {pdf_filename}{Colors.RESET}")
            report = PDFReport(pdf_filename, user_query)
            
            # Add title page
            print(f"{Colors.YELLOW}Adding title page...{Colors.RESET}")
            report.add_title_page()
            
            # Add table of contents
            print(f"{Colors.YELLOW}Adding table of contents...{Colors.RESET}")
            report.add_toc()
            
            # Add executive summary
            print(f"{Colors.YELLOW}Adding executive summary...{Colors.RESET}")
            report.add_section("Executive Summary", exec_summary)
            report.elements.append(PageBreak())
            
            # Add research summary
            print(f"{Colors.YELLOW}Adding research summary...{Colors.RESET}")
            report.add_section("Research Summary", main_content)
            report.elements.append(PageBreak())
            
            # Add citation sections
            print(f"{Colors.YELLOW}Adding {len(citation_contents)} citation sections...{Colors.RESET}")
            for i, citation in enumerate(citation_contents, 1):
                print(f"{Colors.YELLOW}  - Processing citation {i}...{Colors.RESET}")
                report.add_citation_section(i, citation["url"], citation["content"])
            
            # Build the PDF
            print(f"{Colors.YELLOW}Building final PDF document...{Colors.RESET}")
            report.build()
            
            # Verify the PDF was created
            if os.path.exists(pdf_filename) and os.path.getsize(pdf_filename) > 0:
                print(f"{Colors.GREEN}PDF report generated successfully: {pdf_filename}{Colors.RESET}")
            else:
                print(f"{Colors.RED}PDF file not found or is empty after generation attempt.{Colors.RESET}")
                raise FileNotFoundError("PDF file was not created successfully")
        
        except Exception as e:
            print(f"{Colors.RED}Error generating PDF report: {str(e)}{Colors.RESET}")
            import traceback
            traceback.print_exc()  # Print the full stack trace for debugging
            
            # Create a fallback HTML report when PDF fails
            html_filename = os.path.join(reports_dir, "research_report.html")
            print(f"{Colors.YELLOW}Attempting to create HTML report as fallback: {html_filename}{Colors.RESET}")
            
            try:
                with open(html_filename, "w", encoding="utf-8") as f:
                    f.write(f"<html><head><title>Research Report: {user_query}</title>\n")
                    f.write("<style>\n")
                    f.write("body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }\n")
                    f.write("h1 { color: #2c3e50; border-bottom: 1px solid #eee; padding-bottom: 10px; }\n")
                    f.write("h2 { color: #3498db; margin-top: 30px; }\n")
                    f.write("h3 { color: #2980b9; }\n")
                    f.write("a { color: #3498db; text-decoration: none; }\n")
                    f.write(".citation { color: #7f8c8d; font-size: 0.9em; margin-bottom: 20px; }\n")
                    f.write(".toc { background: #f8f9fa; padding: 20px; border-radius: 5px; }\n")
                    f.write("</style></head><body>\n")
                    
                    # Title
                    f.write(f"<h1>Research Report: {user_query}</h1>\n")
                    f.write(f"<p>Generated on: {time.strftime('%B %d, %Y', time.localtime())}</p>\n")
                    
                    # TOC
                    f.write("<div class='toc'><h2>Table of Contents</h2>\n<ul>\n")
                    f.write("<li><a href='#exec_summary'>Executive Summary</a></li>\n")
                    f.write("<li><a href='#research_summary'>Research Summary</a></li>\n")
                    f.write("<li>Citations\n<ul>\n")
                    for i, citation in enumerate(citation_contents, 1):
                        url_parts = urlparse(citation["url"])
                        domain = url_parts.netloc
                        f.write(f"<li><a href='#citation_{i}'>{domain}</a></li>\n")
                    f.write("</ul></li>\n</ul></div>\n")
                    
                    # Executive Summary
                    f.write("<h2 id='exec_summary'>Executive Summary</h2>\n")
                    html_exec_summary = exec_summary.replace("\n", "<br>")
                    f.write(f"<div>{html_exec_summary}</div>\n")
                    
                    # Research Summary
                    f.write("<h2 id='research_summary'>Research Summary</h2>\n")
                    html_main_content = main_content.replace("\n", "<br>")
                    f.write(f"<div>{html_main_content}</div>\n")
                    
                    # Citations
                    for i, citation in enumerate(citation_contents, 1):
                        f.write(f"<h2 id='citation_{i}'>Citation {i}: {citation['url']}</h2>\n")
                        f.write(f"<div class='citation'><a href='{citation['url']}'>{citation['url']}</a></div>\n")
                        html_content = citation['content'].replace("\n", "<br>")
                        f.write(f"<div>{html_content}</div>\n")
                    
                    f.write("</body></html>")
                
                print(f"{Colors.GREEN}HTML report generated successfully as fallback: {html_filename}{Colors.RESET}")
                print(f"{Colors.YELLOW}Please use this HTML report since PDF generation failed.{Colors.RESET}")
            except Exception as html_error:
                print(f"{Colors.RED}Failed to generate HTML fallback report: {str(html_error)}{Colors.RESET}")
            
            print(f"{Colors.YELLOW}Markdown files are still available in the markdown folder.{Colors.RESET}")
        
        # Create a README file with info about this run
        readme_path = os.path.join("markdown", run_folder, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(f"# Research Run: {user_query}\n\n")
            f.write(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n\n")
            f.write(f"**Query**: {user_query}\n\n")
            f.write(f"**Citations Found**: {len(citations)}\n\n")
            f.write(f"**Successfully Processed**: {processed_count}\n\n")
            f.write("## Files in this Research Run\n\n")
            f.write("- `research_summary.md`: The main research results\n")
            f.write("- `executive_summary.md`: Concise executive summary of findings\n")
            f.write("- Citation files: Individual markdown files for each processed citation\n\n")
            f.write("## Complete File Locations\n\n")
            f.write(f"- **Markdown files**: `./markdown/{run_folder}/`\n")
            f.write(f"- **Raw responses**: `./response/{run_folder}/`\n")
            f.write(f"- **PDF report**: `./reports/{run_folder}/research_report.pdf`\n")
        
        print(f"\n{Colors.BOLD}{Colors.GREEN}Processing complete! Successfully processed {processed_count} out of {len(citations)} citations.{Colors.RESET}")
        print(f"{Colors.CYAN}All outputs for this run are in folders named: {run_folder}")
        print(f"- Research summary: {summary_md_path}")
        print(f"- Executive summary: {exec_summary_path}")
        print(f"- PDF report: {pdf_filename}")
        print(f"- Raw responses: ./response/{run_folder}/")
        print(f"- Cleaned markdown: ./markdown/{run_folder}/{Colors.RESET}")

    except requests.exceptions.Timeout:
        print(f"{Colors.RED}The research query timed out. Consider trying again with a more specific query.{Colors.RESET}")
        return
    except Exception as e:
        print(f"{Colors.RED}An error occurred: {str(e)}{Colors.RESET}")
        return

if __name__ == "__main__":
    main()

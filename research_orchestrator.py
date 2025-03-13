#!/usr/bin/env python3
# research_orchestrator.py
"""
Research Orchestrator

This script provides advanced research automation with two modes of operation:

1. Topic-Based Research Mode:
   - Takes a topic, perspective, and depth as inputs
   - Generates multiple research questions using Perplexity
   - Processes each question with efficient citation handling
   - Organizes everything in a topic-based folder structure

2. Direct Question Mode:
   - Takes one or more specific research questions directly
   - Processes each question with efficient citation handling
   - Can also read questions from a file (one per line)

Key Features:
- Parallel processing with configurable worker threads
- Rate limit protection with smart retry logic
- Citation deduplication across questions
- Comprehensive markdown outputs and indexes
- OpenAI file search integration (optional)

OpenAI Integration:
- Automatically uploads research outputs to OpenAI
- Creates vector stores for semantic search
- Tracks research projects in a JSON database
- Enables future applications to search and interact with research

Usage (Topic Mode):
    python research_orchestrator.py --topic "Kahua, the Construction Software Management Company" --perspective "Chief Product Officer" --depth 5

Usage (Direct Question Mode):
    python research_orchestrator.py --questions "What is quantum computing?" "How do quantum computers work?"
    
Usage (Questions from File):
    python research_orchestrator.py --questions questions.txt

OpenAI Integration Options:
    python research_orchestrator.py --topic "AI Ethics" --openai-integration enable
    python research_orchestrator.py --questions "What is climate change?" --openai-integration disable

Run with --help for more options.
"""

import os
import re
import sys
import time
import json
import traceback
import argparse
import threading
import random
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# Try importing OpenAI for file search functionality
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("Warning: OpenAI package not available. File search integration will be disabled.")

# Import functionality from perplexityresearch.py
from perplexityresearch import (
    query_perplexity, 
    intelligent_scrape, 
    clean_thinking_sections, 
    generate_executive_summary
)

# Load environment variables
load_dotenv()

# API Keys
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Models
PERPLEXITY_RESEARCH_MODEL = os.getenv("PERPLEXITY_RESEARCH_MODEL", "sonar-medium-online")
PERPLEXITY_CLEANUP_MODEL = os.getenv("PERPLEXITY_CLEANUP_MODEL", "mixtral-8x7b-instruct")

# API error handling settings
API_MAX_RETRIES = int(os.getenv("API_MAX_RETRIES", 3))
API_INITIAL_RETRY_DELAY = float(os.getenv("API_INITIAL_RETRY_DELAY", 2.0))
API_MAX_RETRY_DELAY = float(os.getenv("API_MAX_RETRY_DELAY", 30.0))

# Rate limiting
RATE_LIMIT_QUESTIONS_PER_WORKER = int(os.getenv("RATE_LIMIT_QUESTIONS_PER_WORKER", 10))
THREAD_STAGGER_DELAY = float(os.getenv("THREAD_STAGGER_DELAY", 5.0))
MAX_CITATIONS = int(os.getenv("MAX_CITATIONS", 50))
CITATION_TIMEOUT = int(os.getenv("CITATION_TIMEOUT", 300))  # Default: 5 minutes

# Project tracking
RESEARCH_PROJECTS_FILE = os.getenv("RESEARCH_PROJECTS_FILE", "research_projects.json")
ENABLE_OPENAI_INTEGRATION = os.getenv("ENABLE_OPENAI_INTEGRATION", "true").lower() in ["true", "yes", "1"]
OPENAI_FILE_UPLOAD_TIMEOUT = int(os.getenv("OPENAI_FILE_UPLOAD_TIMEOUT", "60"))
OPENAI_VECTORSTORE_CREATION_TIMEOUT = int(os.getenv("OPENAI_VECTORSTORE_CREATION_TIMEOUT", "60"))
OPENAI_PROCESSING_MAX_CHECKS = int(os.getenv("OPENAI_PROCESSING_MAX_CHECKS", "20"))
OPENAI_PROCESSING_CHECK_INTERVAL = int(os.getenv("OPENAI_PROCESSING_CHECK_INTERVAL", "10"))

# Thread safety
print_lock = threading.Lock()

# Terminal colors
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"

# Thread-safe print function
def safe_print(message):
    """Thread-safe print function."""
    with print_lock:
        print(message, flush=True)

def with_retry(func, *args, prefix="", **kwargs):
    """
    Wrapper for API calls that implements exponential backoff retry logic.
    Handles rate limiting errors gracefully.
    
    Args:
        func: The function to call
        *args: Arguments to pass to the function
        prefix: Prefix for log messages (e.g., "[Q1]")
        **kwargs: Keyword arguments to pass to the function
        
    Returns:
        The result of the function call
    """
    retry_count = 0
    delay = API_INITIAL_RETRY_DELAY
    
    while True:
        try:
            return func(*args, **kwargs)
        
        except Exception as e:
            error_msg = str(e).lower()
            
            # Check for rate limiting related errors
            is_rate_limit = any(phrase in error_msg for phrase in [
                "rate limit", "too many requests", "429", "throttl", 
                "quota exceeded", "too frequent", "timeout"
            ])
            
            # If we've reached max retries or it's not a rate limit error, raise the exception
            if retry_count >= API_MAX_RETRIES or not is_rate_limit:
                raise
            
            # Exponential backoff for retries
            retry_count += 1
            safe_print(f"{Colors.YELLOW}{prefix} Rate limit detected. Retrying in {delay:.1f} seconds... (Attempt {retry_count}/{API_MAX_RETRIES}){Colors.RESET}")
            time.sleep(delay)
            
            # Increase delay for next retry (exponential backoff with jitter)
            delay = min(delay * 2 * (0.5 + random.random()), API_MAX_RETRY_DELAY)

def research_pipeline(question, master_folder, question_number, total_questions, topic=None, perspective=None):
    """
    Phase 1 of the research process: Get initial research response for a question.
    Modified to stop after getting the research response - does not process citations.
    
    Args:
        question: The research question to process
        master_folder: Path to the master folder for output
        question_number: The question number (for logging and file names)
        total_questions: Total number of questions (for progress reporting)
        topic: Optional overall research topic for context
        perspective: Optional professional perspective for context
    
    Returns:
        A tuple of (success_flag, research_response, citations)
    """
    safe_print(f"\n{Colors.BOLD}{Colors.MAGENTA}[{question_number}/{total_questions}] Researching: '{question}'{Colors.RESET}")
    
    try:
        # Set up paths for this question
        safe_question = re.sub(r'[^\w\s-]', '', question)
        safe_question = re.sub(r'[-\s]+', '_', safe_question).strip('-_')
        safe_question = safe_question[:30] if len(safe_question) > 30 else safe_question
        
        # Change prefix from Q to A to make files sort to the top
        question_prefix = f"A{question_number:02d}_"
        prefix = f"[Q{question_number}]"  # Keep this as Q for log messages for clarity
        
        # Timestamp not needed for individual questions as they're in the master folder
        response_dir = os.path.join(master_folder, "response")
        markdown_dir = os.path.join(master_folder, "markdown")
        
        # STEP 1: Call Perplexity with 'research' model for the initial research
        safe_print(f"{Colors.CYAN}{prefix} Starting research...{Colors.RESET}")
        
        # Create enhanced prompt with context if topic is provided
        if topic:
            context_prompt = f"""
Research Question: {question}

CONTEXT:
- Overall Research Topic: {topic}
- Professional Perspective: {perspective or "Researcher"}

Please perform comprehensive, detailed research on the question above, considering the overall research topic and professional perspective provided in the context. Your answer should be thorough, well-structured, and directly relevant to both the specific question and the broader research goals.
"""
        else:
            context_prompt = question
            
        research_response = with_retry(
            query_perplexity,
            prompt=context_prompt,
            model=PERPLEXITY_RESEARCH_MODEL,
            system_prompt="You are a professional researcher providing comprehensive, accurate information. Focus on delivering a thorough analysis that considers both the specific question and its context within the broader research topic.",
            is_research=True,
            prefix=prefix
        )
        
        # Dump the main research response JSON to file
        research_filename = os.path.join(response_dir, f"{question_prefix}research_response.json")
        with open(research_filename, "w", encoding="utf-8") as f:
            json.dump(research_response, f, indent=2)
        safe_print(f"{Colors.GREEN}{prefix} Saved main Perplexity research response.{Colors.RESET}")
        
        # STEP 2: Extract citations from the root level of the response
        citations = research_response.get("citations", [])
        safe_print(f"{Colors.BOLD}{Colors.CYAN}{prefix} Found {len(citations)} citation(s).{Colors.RESET}")

        # Get the main content and clean it of thinking sections
        main_content = ""
        if research_response.get("choices") and len(research_response["choices"]) > 0:
            raw_content = research_response["choices"][0]["message"].get("content", "")
            main_content = clean_thinking_sections(raw_content)
        
        # Save the cleaned research summary
        summary_md_path = os.path.join(markdown_dir, f"{question_prefix}research_summary.md")
        with open(summary_md_path, "w", encoding="utf-8") as f:
            f.write(f"# Research Summary: {question}\n\n")
            # Make sure the content is clean of thinking sections again before writing
            cleaned_content = clean_thinking_sections(main_content)
            f.write(cleaned_content)
            f.write("\n\n## Citation Links\n\n")
            for i, url in enumerate(citations, 1):
                f.write(f"{i}. [{url}]({url})\n")
        safe_print(f"{Colors.GREEN}{prefix} Saved research summary.{Colors.RESET}")

        # Generate executive summary
        safe_print(f"{Colors.CYAN}{prefix} Generating executive summary...{Colors.RESET}")
        exec_summary = with_retry(
            generate_executive_summary,
            question, main_content, PERPLEXITY_CLEANUP_MODEL,
            prefix=prefix
        )
        
        # Save the executive summary separately with new naming convention
        exec_summary_prefix = f"ES{question_number}_"
        exec_summary_path = os.path.join(markdown_dir, f"{exec_summary_prefix}executive_summary.md")
        with open(exec_summary_path, "w", encoding="utf-8") as f:
            f.write(f"# Executive Summary: {question}\n\n")
            # Clean the executive summary of any thinking sections before writing
            cleaned_exec_summary = clean_thinking_sections(exec_summary)
            f.write(cleaned_exec_summary)
        safe_print(f"{Colors.GREEN}{prefix} Saved executive summary.{Colors.RESET}")

        # Create a question metadata file with citations
        metadata = {
            "question": question,
            "question_number": question_number,
            "citations": citations,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }
        metadata_path = os.path.join(response_dir, f"{question_prefix}metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        
        safe_print(f"\n{Colors.BOLD}{Colors.GREEN}{prefix} Question research phase completed successfully.{Colors.RESET}")
        return True, research_response, citations
    
    except Exception as e:
        safe_print(f"{Colors.RED}Error in research pipeline for question {question_number}: {str(e)}{Colors.RESET}")
        with print_lock:
            traceback.print_exc()
        return False, None, []

def create_master_index(master_folder, questions, results):
    """
    Create a master index of all questions and their research outputs.
    
    Args:
        master_folder: The master folder path
        questions: List of research questions
        results: List of (success_flag, research_response, citations) tuples
    
    Returns:
        Path to the created index file
    """
    markdown_dir = os.path.join(master_folder, "markdown")
    index_path = os.path.join(markdown_dir, "master_index.md")
    
    # Get timestamp for the report
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# Research Project Results\n\n")
        f.write(f"Generated on: {timestamp}\n\n")
        
        # Count of successful questions
        successful_count = sum(1 for success, _, _ in results if success)
        f.write(f"Successfully processed {successful_count} out of {len(questions)} questions.\n\n")
        
        # Table of contents
        f.write("## Table of Contents\n\n")
        for i, question in enumerate(questions, 1):
            success = results[i-1][0] if i-1 < len(results) else False
            status = "✅" if success else "❌"
            # Clean any thinking sections from the question
            clean_question = clean_thinking_sections(question)
            f.write(f"{i}. {status} [Q{i:02d}: {clean_question[:80]}{'...' if len(clean_question) > 80 else ''}](#q{i:02d})\n")
        
        f.write("\n---\n\n")
        
        # Question details
        for i, question in enumerate(questions, 1):
            f.write(f"## Q{i:02d}\n\n")
            success, research_response, citations = results[i-1] if i-1 < len(results) else (False, None, [])
            
            # Clean any thinking sections from the question
            clean_question = clean_thinking_sections(question)
            f.write(f"**Question**: {clean_question}\n\n")
            
            if not success:
                f.write("**Status**: ❌ Processing failed\n\n")
                continue
                
            f.write("**Status**: ✅ Successfully processed\n\n")
            
            # Citations
            if citations:
                f.write(f"**Citations**: {len(citations)}\n\n")
                f.write("| # | Citation URL |\n")
                f.write("|---|-------------|\n")
                for j, citation in enumerate(citations, 1):
                    f.write(f"| {j} | [{citation[:60]}...]({citation}) |\n")
            else:
                f.write("**Citations**: None found\n\n")
                
            # Links to outputs - updated to use A prefix instead of Q
            question_prefix = f"A{i:02d}_"
            f.write("\n**Research Outputs**:\n\n")
            f.write(f"- [Research Summary]({question_prefix}research_summary.md)\n")
            f.write(f"- [Executive Summary]({question_prefix}executive_summary.md)\n")
            
            f.write("\n---\n\n")
    
    safe_print(f"{Colors.GREEN}Created master index at {index_path}{Colors.RESET}")
    return index_path

def process_citation(citation_url, question_context, master_folder, citation_id, total_citations, prefix=""):
    """
    Phase 3: Process a unique citation once.
    
    Args:
        citation_url: The URL to process
        question_context: Context about questions using this citation
        master_folder: The master folder for output
        citation_id: Unique ID for this citation
        total_citations: Total number of unique citations
        prefix: Prefix for log messages
        
    Returns:
        Dictionary with citation processing results
    """
    safe_print(f"\n{Colors.BOLD}{Colors.BLUE}{prefix} [{citation_id}/{total_citations}] Processing citation: {citation_url}{Colors.RESET}")
    
    try:
        response_dir = os.path.join(master_folder, "response")
        markdown_dir = os.path.join(master_folder, "markdown")
        
        # Use the unique citation_id for file naming
        citation_prefix = f"C{citation_id:03d}_"
        
        # Generate a summary of questions using this citation for context
        questions_summary = "\n".join([f"- {q['question']} (Q{q['question_number']:02d})" for q in question_context])
        context_info = f"This citation was referenced by {len(question_context)} research questions:\n{questions_summary}"
        
        # Import the FirecrawlApp client directly
        from firecrawl import FirecrawlApp
        firecrawl_client = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
        
        # Intelligently scrape the URL with retry logic
        citation_log_prefix = f"{prefix} [Citation {citation_id}]"
        result = with_retry(
            intelligent_scrape,
            citation_url, context_info, 
            prefix=citation_log_prefix
        )
        
        # Save the raw Firecrawl response
        raw_json_path = os.path.join(response_dir, f"{citation_prefix}firecrawl.json")
        with open(raw_json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        safe_print(f"{Colors.GREEN}{prefix} Saved Firecrawl response for citation {citation_id}.{Colors.RESET}")
        
        # Clean the response with Perplexity
        content_for_cleanup = result.get("markdown") or ""
        if not content_for_cleanup:
            safe_print(f"{Colors.YELLOW}{prefix} No textual content found for cleanup. Skipping.{Colors.RESET}")
            cleaned_content = "No content available."
        else:
            safe_print(f"{Colors.YELLOW}{prefix} Cleaning up content with Perplexity...{Colors.RESET}")
            # Limit content size to avoid token limits
            truncated_content = content_for_cleanup[:30000] if len(content_for_cleanup) > 30000 else content_for_cleanup
            
            cleanup_prompt = f"""
Rewrite the following content more cleanly as Markdown.
Make it structured, neat, and well-formatted.
This is from the URL: {citation_url}

{truncated_content}
"""
            cleanup_response = with_retry(
                query_perplexity,
                prompt=cleanup_prompt,
                model=PERPLEXITY_CLEANUP_MODEL,
                system_prompt="You are a Markdown formatter. Return only well-structured Markdown with clear headings, proper lists, and good organization of information.",
                prefix=citation_log_prefix
            )
            
            # Extract the final text from cleanup
            if cleanup_response.get("choices") and len(cleanup_response["choices"]) > 0:
                cleaned_content = cleanup_response["choices"][0]["message"].get("content", "")
                # Ensure we clean any thinking sections from the content
                cleaned_content = clean_thinking_sections(cleaned_content)
            else:
                cleaned_content = "Error: Failed to get cleaned content from Perplexity."
        
        # Save the cleaned markdown
        md_filename = os.path.join(markdown_dir, f"{citation_prefix}citation.md")
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(f"# Citation {citation_id}: {citation_url}\n\n")
            f.write(f"## Referenced by\n\n{questions_summary}\n\n")
            f.write("## Content\n\n")
            # Ensure we clean any thinking sections from the content again before writing
            final_cleaned_content = clean_thinking_sections(cleaned_content)
            f.write(final_cleaned_content)
        safe_print(f"{Colors.GREEN}{prefix} Saved cleaned Markdown for citation {citation_id}.{Colors.RESET}")
        
        # Create citation metadata
        citation_metadata = {
            "citation_id": citation_id,
            "url": citation_url,
            "questions": question_context,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }
        metadata_path = os.path.join(response_dir, f"{citation_prefix}metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(citation_metadata, f, indent=2)
        
        return {
            "citation_id": citation_id,
            "url": citation_url,
            "success": True,
            "content": cleaned_content
        }
        
    except Exception as e:
        error_msg = str(e)
        safe_print(f"{Colors.RED}{prefix} Error processing citation #{citation_id}: {error_msg}{Colors.RESET}")
        
        # Create a special error message for the citation
        error_content = f"# Error Processing Citation\n\n**URL**: {citation_url}\n\n"
        error_content += f"**Error details**: {error_msg}\n\n"
        error_content += f"## Referenced by\n\n{questions_summary}\n\n"
        
        return {
            "citation_id": citation_id,
            "url": citation_url,
            "success": False,
            "content": error_content,
            "error": error_msg
        }

def extract_and_deduplicate_citations(all_questions_results):
    """
    Phase 2: Extract and deduplicate all citations from research responses.
    Also ranks citations by frequency of reference.
    
    Args:
        all_questions_results: List of tuples (success_flag, research_response, citations)
    
    Returns:
        Dictionary mapping unique citations to lists of question data
    """
    citation_map = {}
    
    for idx, (success, response, citations) in enumerate(all_questions_results):
        if not success or not citations:
            continue
            
        question_number = idx + 1
        question = None
        
        # Extract the question from the response
        if response and "prompt" in response:
            question = response["prompt"]
        
        # If we couldn't extract the question, use a placeholder
        if not question:
            question = f"Question {question_number}"
            
        # Add each citation to the map
        for citation in citations:
            if citation not in citation_map:
                citation_map[citation] = []
                
            citation_map[citation].append({
                "question": question,
                "question_number": question_number
            })
    
    return citation_map

def prioritize_citations(citation_map, max_citations=50):
    """
    Prioritize citations based on frequency of reference and return top N.
    
    Args:
        citation_map: Dictionary mapping citations to list of referencing questions
        max_citations: Maximum number of citations to process
        
    Returns:
        Tuple of (prioritized_citations, skipped_count)
    """
    # Sort citations by number of references (descending)
    sorted_citations = sorted(
        citation_map.items(), 
        key=lambda item: len(item[1]), 
        reverse=True
    )
    
    # Take top N citations
    prioritized_citations = dict(sorted_citations[:max_citations])
    skipped_count = len(sorted_citations) - max_citations if len(sorted_citations) > max_citations else 0
    
    return prioritized_citations, skipped_count

# Add a new timeout wrapper function
def with_timeout(func, timeout, *args, **kwargs):
    """
    Run a function with a timeout. If the function doesn't complete within
    the timeout, return an error result.
    
    Args:
        func: The function to call
        timeout: Timeout in seconds
        *args: Arguments to pass to the function
        **kwargs: Keyword arguments to pass to the function
        
    Returns:
        Result of the function or an error result if timeout occurs
    """
    import threading
    import queue
    
    # First, verify citation_url is valid if we're processing a citation
    if func == process_citation and args:
        citation_url = args[0]
        citation_id = args[3] if len(args) > 3 else "unknown"
        
        # If citation_url is not a string, return an error
        if not isinstance(citation_url, str):
            return {
                "citation_id": citation_id,
                "url": str(citation_url),  # Convert to string for display
                "success": False,
                "content": f"# Error Processing Citation\n\n**Error details**: Invalid citation URL type: {type(citation_url).__name__}\n\n",
                "error": f"Invalid citation URL type: {type(citation_url).__name__}"
            }
        
        # Basic URL validation
        if not citation_url.startswith(('http://', 'https://')):
            return {
                "citation_id": citation_id,
                "url": citation_url,
                "success": False,
                "content": f"# Error Processing Citation\n\n**Error details**: Invalid URL format: {citation_url}\n\n",
                "error": f"Invalid URL format: {citation_url}"
            }
    
    result_queue = queue.Queue()
    
    def worker():
        try:
            result = func(*args, **kwargs)
            result_queue.put(("success", result))
        except Exception as e:
            result_queue.put(("error", str(e)))
    
    thread = threading.Thread(target=worker)
    thread.daemon = True
    thread.start()
    
    try:
        status, result = result_queue.get(timeout=timeout)
        if status == "error":
            raise Exception(result)
        return result
    except queue.Empty:
        # Timeout occurred
        citation_id = args[3] if len(args) > 3 else "unknown"
        citation_url = args[0] if args else "unknown"
        return {
            "citation_id": citation_id,
            "url": citation_url,
            "success": False,
            "content": f"# Error Processing Citation\n\n**URL**: {citation_url}\n\n**Error details**: Processing timed out after {timeout} seconds\n\n",
            "error": f"Timeout after {timeout} seconds"
        }

def create_citation_index(master_folder, citation_map, citation_results, skipped_count=0):
    """
    Create an index of all citations in Markdown format.
    
    Args:
        master_folder: The master folder for output
        citation_map: Mapping of citations to questions (full map)
        citation_results: Results from processing citations (prioritized ones)
        skipped_count: Number of citations that were skipped due to prioritization
    """
    markdown_dir = os.path.join(master_folder, "markdown")
    citation_index_path = os.path.join(markdown_dir, "citation_index.md")
    
    with open(citation_index_path, "w", encoding="utf-8") as f:
        f.write("# Citation Index\n\n")
        f.write("This document indexes all unique citations found during research.\n\n")
        
        if skipped_count > 0:
            f.write(f"**Note**: {skipped_count} less referenced citations were not processed to optimize API usage and processing time.\n\n")
        
        # Create sections
        f.write("## Processed Citations\n\n")
        f.write("These citations were processed and have content available:\n\n")
        
        # Create a table of contents for processed citations
        for i, citation_result in enumerate(citation_results, 1):
            citation_url = citation_result["url"]
            success_mark = "✅" if citation_result["success"] else "❌"
            ref_count = len(citation_map.get(citation_url, []))
            f.write(f"{i}. {success_mark} [Citation {i}: {citation_url[:60]}...](#citation-{i}) (Referenced by {ref_count} questions)\n")
        
        f.write("\n---\n\n")
        
        # Add detailed entries for each processed citation
        for i, citation_result in enumerate(citation_results, 1):
            citation_url = citation_result["url"]
            success_status = "Successfully processed" if citation_result["success"] else "Processing failed"
            
            f.write(f"## Citation {i}\n\n")
            f.write(f"**URL**: [{citation_url}]({citation_url})\n\n")
            f.write(f"**Status**: {success_status}\n\n")
            
            # List referencing questions
            questions = citation_map.get(citation_url, [])
            f.write(f"**Referenced by {len(questions)} questions**:\n\n")
            for q in questions:
                # Clean any thinking sections from the question
                clean_question = clean_thinking_sections(q['question'])
                f.write(f"- Q{q['question_number']:02d}: {clean_question}\n")
            
            f.write("\n---\n\n")
        
        # Create a section for skipped citations if any
        if skipped_count > 0:
            f.write("## Skipped Citations\n\n")
            f.write("These citations were found but not processed due to prioritization:\n\n")
            
            # Get URLs of processed citations
            processed_urls = [result["url"] for result in citation_results]
            
            # Find skipped citations
            skipped_citation_items = [
                (url, refs) for url, refs in citation_map.items() 
                if url not in processed_urls
            ]
            
            # Sort by reference count (descending)
            skipped_citation_items.sort(key=lambda x: len(x[1]), reverse=True)
            
            # List them
            for i, (url, refs) in enumerate(skipped_citation_items, 1):
                ref_count = len(refs)
                f.write(f"{i}. [{url[:60]}...]({url}) (Referenced by {ref_count} questions)\n")
    
    safe_print(f"{Colors.GREEN}Created citation index with all {len(citation_map)} citations.{Colors.RESET}")
    return citation_index_path

def consolidate_summary_files(master_folder, pattern, output_filename, title):
    """
    Consolidate all files matching a pattern in the markdown directory into a single file.
    
    Args:
        master_folder: The master folder for the research project
        pattern: Regex pattern to match filenames (e.g., "executive_summary")
        output_filename: Name of the output file
        title: Title for the consolidated file
    
    Returns:
        Path to the consolidated file
    """
    markdown_dir = os.path.join(master_folder, "markdown")
    summaries_dir = os.path.join(master_folder, "summaries")
    os.makedirs(summaries_dir, exist_ok=True)
    
    # Find all files matching the pattern
    summary_files = []
    for filename in os.listdir(markdown_dir):
        if pattern in filename.lower() and filename.endswith(".md"):
            summary_files.append(filename)
    
    # Sort files by question number (handling both A01_ and ES1_ formats)
    def extract_question_number(filename):
        # Try to match ES{number}_ format first (for executive summaries)
        es_match = re.search(r'ES(\d+)_', filename)
        if es_match:
            return int(es_match.group(1))
        
        # Try to match A{number}_ format next (for research summaries)
        a_match = re.search(r'A(\d+)_', filename)
        if a_match:
            return int(a_match.group(1))
            
        # If neither pattern matches, put at the end
        return 999
    
    summary_files.sort(key=extract_question_number)
    
    # Create a consolidated markdown file
    output_file = os.path.join(summaries_dir, output_filename)
    
    # Write a header for the consolidated file
    with open(output_file, 'w', encoding='utf-8') as outfile:
        outfile.write(f"# {title}\n\n")
        outfile.write(f"This document contains all {pattern} files from the research project.\n\n")
        outfile.write("---\n\n")
        
        # Read and append each summary file
        for i, filename in enumerate(summary_files, 1):
            file_path = os.path.join(markdown_dir, filename)
            
            # Extract question number if available (handle both formats)
            question_num = None
            es_match = re.search(r'ES(\d+)_', filename)
            if es_match:
                question_num = es_match.group(1)
            else:
                a_match = re.search(r'A(\d+)_', filename)
                if a_match:
                    question_num = a_match.group(1)
                    
            question_label = f"Question {question_num}" if question_num else f"Summary {i}"
            
            # Add a divider between summaries (except before the first one)
            if i > 1:
                outfile.write("\n\n---\n\n")
            
            outfile.write(f"## {question_label}\n\n")
            
            # Read and append the file content, skipping the original title
            with open(file_path, 'r', encoding='utf-8') as infile:
                content = infile.read()
                
                # Skip the original title (assumes first line is a markdown title)
                lines = content.split('\n')
                if lines and lines[0].startswith('# '):
                    content = '\n'.join(lines[1:])
                
                # Clean any thinking sections from the content
                content = clean_thinking_sections(content)
                
                outfile.write(content.strip())
    
    safe_print(f"{Colors.GREEN}Consolidated {len(summary_files)} {pattern} files into: {output_file}{Colors.RESET}")
    return output_file

def move_file(source_path, dest_dir):
    """
    Move a file from source path to destination directory.
    
    Args:
        source_path: Path to the source file
        dest_dir: Destination directory
        
    Returns:
        Path to the moved file
    """
    os.makedirs(dest_dir, exist_ok=True)
    filename = os.path.basename(source_path)
    dest_path = os.path.join(dest_dir, filename)
    
    # Read the original file
    with open(source_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Write to the new location
    with open(dest_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # If write was successful, delete the original file (optional)
    # os.remove(source_path)
    
    safe_print(f"{Colors.GREEN}Moved {filename} to {dest_dir}{Colors.RESET}")
    return dest_path

def generate_research_questions(topic, perspective, depth):
    """
    Generates research questions using Perplexity API.
    Returns a list of questions.
    """
    safe_print(f"{Colors.BLUE}Generating research questions for topic: {topic}{Colors.RESET}")
    safe_print(f"{Colors.BLUE}From perspective: {perspective}{Colors.RESET}")
    safe_print(f"{Colors.BLUE}Number of questions requested: {depth}{Colors.RESET}")
    
    # Construct the prompt for Perplexity
    prompt = f"""
I'm going to be doing deep research on {topic}. From the perspective of a {perspective}. 
Give me {depth} interesting research questions to dive into. 

Embed each question in [[[<question>]]] and just show the {depth} questions with no other text. 
Start from the most general questions ("What is {topic}?") to increasingly specific questions that are relevant based on the perspective of a {perspective}. 
Good research on a company, as an example, would focus on competitors as well the industry and other key factors. It might also ask specificquestions about each module of the software. Research on other topics should be similarly thorough.
First think deeply and mentally mind map this project deeply across all facets then begin.
"""
    
    # Query Perplexity for questions
    try:
        # Use the retry wrapper for API call
        response = with_retry(
            query_perplexity,
            prompt=prompt,
            model=PERPLEXITY_RESEARCH_MODEL,
            system_prompt="You are a professional research question generator. Create insightful and specific questions.",
            is_research=False,  # We don't need the long timeout for this
            prefix="[Question Generation]"
        )
        
        # Extract the content from the response
        content = ""
        if response.get("choices") and len(response["choices"]) > 0:
            content = response["choices"][0]["message"].get("content", "")
        
        # Clean any thinking sections
        content = clean_thinking_sections(content)
        
        # Extract questions using regex
        questions = re.findall(r'\[\[\[(.*?)\]\]\]', content, re.DOTALL)
        
        # Clean up questions (remove extra whitespace, etc.)
        questions = [q.strip() for q in questions]
        
        safe_print(f"{Colors.GREEN}Generated {len(questions)} research questions.{Colors.RESET}")
        return questions
        
    except Exception as e:
        safe_print(f"{Colors.RED}Error generating research questions: {str(e)}{Colors.RESET}")
        return []

def load_project_tracking():
    """
    Load the project tracking data from JSON file.
    If file doesn't exist, create a new one with default structure.
    
    Returns:
        dict: The project tracking data
    """
    if os.path.exists(RESEARCH_PROJECTS_FILE):
        try:
            with open(RESEARCH_PROJECTS_FILE, "r") as f:
                data = json.load(f)
                return data
        except Exception as e:
            safe_print(f"{Colors.YELLOW}Warning: Could not load project tracking file: {str(e)}{Colors.RESET}")
            
    # Create new tracking file with default structure
    data = {
        "version": "1.0",
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "projects": []
    }
    
    # Save the new file
    try:
        with open(RESEARCH_PROJECTS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        safe_print(f"{Colors.YELLOW}Warning: Could not create project tracking file: {str(e)}{Colors.RESET}")
    
    return data

def save_project_tracking(data):
    """
    Save the project tracking data to JSON file.
    
    Args:
        data (dict): The project tracking data to save
    
    Returns:
        bool: True if successful, False otherwise
    """
    # Update the last_updated timestamp
    data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    try:
        with open(RESEARCH_PROJECTS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        safe_print(f"{Colors.YELLOW}Warning: Could not save project tracking file: {str(e)}{Colors.RESET}")
        return False

def add_project_to_tracking(project_data):
    """
    Add a new project to the tracking file.
    
    Args:
        project_data (dict): Data about the research project
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Load existing tracking data
        tracking_data = load_project_tracking()
        
        # Add the new project
        tracking_data["projects"].append(project_data)
        
        # Save the updated tracking data
        return save_project_tracking(tracking_data)
    except Exception as e:
        safe_print(f"{Colors.YELLOW}Warning: Could not add project to tracking file: {str(e)}{Colors.RESET}")
        return False

def update_project_in_tracking(project_id, updates):
    """
    Update an existing project in the tracking file.
    
    Args:
        project_id (str): The ID of the project to update
        updates (dict): The data to update
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Load existing tracking data
        tracking_data = load_project_tracking()
        
        # Find the project by ID
        for project in tracking_data["projects"]:
            if project.get("id") == project_id:
                # Update the project data
                project.update(updates)
                # Save the updated tracking data
                return save_project_tracking(tracking_data)
        
        safe_print(f"{Colors.YELLOW}Warning: Project with ID {project_id} not found in tracking file{Colors.RESET}")
        return False
    except Exception as e:
        safe_print(f"{Colors.YELLOW}Warning: Could not update project in tracking file: {str(e)}{Colors.RESET}")
        return False

def create_openai_client():
    """
    Create an OpenAI client instance if OpenAI is available.
    
    Returns:
        OpenAI client or None if not available
    """
    if not OPENAI_AVAILABLE:
        safe_print(f"{Colors.YELLOW}OpenAI integration is not available: OpenAI package not installed{Colors.RESET}")
        return None
        
    if not OPENAI_API_KEY:
        safe_print(f"{Colors.YELLOW}OpenAI integration is not available: OPENAI_API_KEY not set in environment{Colors.RESET}")
        return None
        
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        return client
    except Exception as e:
        safe_print(f"{Colors.RED}Error creating OpenAI client: {str(e)}{Colors.RESET}")
        return None

def upload_file_to_openai(client, file_path, prefix=""):
    """
    Upload a file to OpenAI's API.
    
    Args:
        client: The OpenAI client
        file_path: Path to the file to upload
        prefix: Prefix for log messages
        
    Returns:
        The file ID or None if failed
    """
    try:
        safe_print(f"{Colors.CYAN}{prefix} Uploading file: {file_path}{Colors.RESET}")
        
        with open(file_path, "rb") as file_content:
            result = client.files.create(
                file=file_content,
                purpose="assistants"
            )
            
        safe_print(f"{Colors.GREEN}{prefix} Successfully uploaded file: {file_path}, File ID: {result.id}{Colors.RESET}")
        return result.id
    except Exception as e:
        safe_print(f"{Colors.RED}{prefix} Error uploading file {file_path}: {str(e)}{Colors.RESET}")
        return None

def upload_files_to_openai(client, project_folder, project_id, prefix=""):
    """
    Upload multiple files from a project folder to OpenAI.
    
    Args:
        client: The OpenAI client
        project_folder: The folder containing the research project
        project_id: The ID of the project for tracking
        prefix: Prefix for log messages
        
    Returns:
        Dictionary with uploaded file IDs
    """
    if not client:
        return None
        
    try:
        file_ids = {
            "readme": None,
            "markdown_files": [],
            "summary_files": []
        }
        
        # Upload README.md if it exists
        readme_path = os.path.join(project_folder, "README.md")
        if os.path.exists(readme_path):
            file_id = upload_file_to_openai(client, readme_path, prefix)
            if file_id:
                file_ids["readme"] = file_id
        
        # Upload markdown files
        markdown_folder = os.path.join(project_folder, "markdown")
        if os.path.exists(markdown_folder):
            for filename in os.listdir(markdown_folder):
                if filename.endswith(".md"):
                    file_path = os.path.join(markdown_folder, filename)
                    file_id = upload_file_to_openai(client, file_path, prefix)
                    if file_id:
                        file_ids["markdown_files"].append(file_id)
        
        # Upload summary files
        summaries_folder = os.path.join(project_folder, "summaries")
        if os.path.exists(summaries_folder):
            for filename in os.listdir(summaries_folder):
                if filename.endswith(".md"):
                    file_path = os.path.join(summaries_folder, filename)
                    file_id = upload_file_to_openai(client, file_path, prefix)
                    if file_id:
                        file_ids["summary_files"].append(file_id)
        
        # Update project tracking with file IDs
        update_data = {
            "openai_integration": {
                "file_ids": file_ids
            }
        }
        update_project_in_tracking(project_id, update_data)
        
        return file_ids
    except Exception as e:
        safe_print(f"{Colors.RED}{prefix} Error uploading files: {str(e)}{Colors.RESET}")
        return None

def create_vector_store(client, name, prefix=""):
    """
    Create a vector store with OpenAI.
    
    Args:
        client: The OpenAI client
        name: Name for the vector store
        prefix: Prefix for log messages
        
    Returns:
        The vector store object or None if failed
    """
    if not client:
        return None
        
    try:
        safe_print(f"{Colors.CYAN}{prefix} Creating vector store: {name}{Colors.RESET}")
        vector_store = client.vector_stores.create(name=name)
        safe_print(f"{Colors.GREEN}{prefix} Vector store created with ID: {vector_store.id}{Colors.RESET}")
        return vector_store
    except Exception as e:
        safe_print(f"{Colors.RED}{prefix} Error creating vector store: {str(e)}{Colors.RESET}")
        return None

def add_files_to_vector_store(client, vector_store_id, file_ids, prefix=""):
    """
    Add multiple files to a vector store.
    
    Args:
        client: The OpenAI client
        vector_store_id: ID of the vector store
        file_ids: List of file IDs to add
        prefix: Prefix for log messages
        
    Returns:
        Number of files successfully added
    """
    if not client or not vector_store_id or not file_ids:
        return 0
        
    added_count = 0
    
    for file_id in file_ids:
        try:
            client.vector_stores.files.create(
                vector_store_id=vector_store_id,
                file_id=file_id
            )
            safe_print(f"{Colors.GREEN}{prefix} Added file {file_id} to vector store{Colors.RESET}")
            added_count += 1
        except Exception as e:
            safe_print(f"{Colors.RED}{prefix} Error adding file {file_id} to vector store: {str(e)}{Colors.RESET}")
    
    return added_count

def check_files_processing_status(client, vector_store_id, prefix=""):
    """
    Check the processing status of files in a vector store.
    
    Args:
        client: The OpenAI client
        vector_store_id: ID of the vector store
        prefix: Prefix for log messages
        
    Returns:
        True if all files are processed, False otherwise
    """
    if not client or not vector_store_id:
        return False
        
    try:
        result = client.vector_stores.files.list(vector_store_id=vector_store_id)
        
        all_completed = True
        processed_count = 0
        
        for file in result.data:
            if file.status == "completed":
                processed_count += 1
            else:
                all_completed = False
                
        total_files = len(result.data)
        safe_print(f"{Colors.CYAN}{prefix} Processing status: {processed_count}/{total_files} files completed{Colors.RESET}")
        
        return all_completed
    except Exception as e:
        safe_print(f"{Colors.RED}{prefix} Error checking file status: {str(e)}{Colors.RESET}")
        return False

def process_files_with_openai(master_folder, project_data):
    """
    Process research files with OpenAI: upload files, create vector store, and update tracking.
    
    Args:
        master_folder: Folder containing the research project
        project_data: Project data dictionary
        
    Returns:
        Updated project data with OpenAI integration info
    """
    if not ENABLE_OPENAI_INTEGRATION:
        safe_print(f"{Colors.YELLOW}OpenAI integration is disabled. Set ENABLE_OPENAI_INTEGRATION=true to enable.{Colors.RESET}")
        project_data["openai_integration"] = {"status": "disabled"}
        return project_data
        
    if not OPENAI_AVAILABLE:
        safe_print(f"{Colors.YELLOW}OpenAI package is not installed. Run 'pip install openai' to enable this feature.{Colors.RESET}")
        project_data["openai_integration"] = {"status": "unavailable", "reason": "openai package not installed"}
        return project_data
        
    if not OPENAI_API_KEY:
        safe_print(f"{Colors.YELLOW}OPENAI_API_KEY is not set in environment. Add it to your .env file.{Colors.RESET}")
        project_data["openai_integration"] = {"status": "unavailable", "reason": "api key not configured"}
        return project_data
        
    # Create OpenAI client
    client = create_openai_client()
    if not client:
        safe_print(f"{Colors.RED}Failed to create OpenAI client. OpenAI integration will be skipped.{Colors.RESET}")
        project_data["openai_integration"] = {"status": "error", "reason": "client creation failed"}
        return project_data
    
    prefix = "[OpenAI]"
    safe_print(f"\n{Colors.BOLD}{Colors.CYAN}======== PHASE 5: OPENAI FILE PROCESSING ========{Colors.RESET}")
    
    # Get project ID
    project_id = project_data.get("id")
    if not project_id:
        safe_print(f"{Colors.RED}{prefix} Project ID not found in project data. OpenAI integration will be skipped.{Colors.RESET}")
        project_data["openai_integration"] = {"status": "error", "reason": "project id missing"}
        return project_data
    
    # Step 1: Upload files to OpenAI
    safe_print(f"{Colors.CYAN}{prefix} Uploading files to OpenAI...{Colors.RESET}")
    file_ids = upload_files_to_openai(client, master_folder, project_id, prefix)
    
    if not file_ids:
        safe_print(f"{Colors.RED}{prefix} Failed to upload files to OpenAI.{Colors.RESET}")
        project_data["openai_integration"] = {"status": "error", "reason": "file upload failed"}
        return project_data
    
    # Count total uploaded files
    total_files = (1 if file_ids["readme"] else 0) + len(file_ids["markdown_files"]) + len(file_ids["summary_files"])
    safe_print(f"{Colors.GREEN}{prefix} Successfully uploaded {total_files} files to OpenAI.{Colors.RESET}")
    
    # If no files were uploaded, skip vector store creation
    if total_files == 0:
        safe_print(f"{Colors.YELLOW}{prefix} No files were uploaded. Skipping vector store creation.{Colors.RESET}")
        project_data["openai_integration"] = {"status": "no_files", "file_ids": file_ids}
        return project_data
    
    # Step 2: Create vector store
    # Generate a name for the vector store based on topic or questions
    if project_data.get("parameters", {}).get("topic"):
        topic = project_data["parameters"]["topic"]
    else:
        # Use first question as topic (truncated)
        first_question = project_data.get("parameters", {}).get("questions", ["Research"])[0]
        topic = first_question[:30].replace("?", "").strip()
    
    timestamp = project_data.get("timestamp", "").replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
    vector_store_name = f"{topic}_{timestamp}".replace(" ", "_")[:50]
    
    safe_print(f"{Colors.CYAN}{prefix} Creating vector store: {vector_store_name}{Colors.RESET}")
    vector_store = create_vector_store(client, vector_store_name, prefix)
    
    if not vector_store:
        safe_print(f"{Colors.RED}{prefix} Failed to create vector store.{Colors.RESET}")
        project_data["openai_integration"] = {
            "status": "partial",
            "file_ids": file_ids,
            "reason": "vector store creation failed"
        }
        return project_data
    
    # Step 3: Add files to vector store
    safe_print(f"{Colors.CYAN}{prefix} Adding files to vector store...{Colors.RESET}")
    
    # Collect all file IDs
    all_file_ids = []
    if file_ids["readme"]:
        all_file_ids.append(file_ids["readme"])
    all_file_ids.extend(file_ids["markdown_files"])
    all_file_ids.extend(file_ids["summary_files"])
    
    added_count = add_files_to_vector_store(client, vector_store.id, all_file_ids, prefix)
    safe_print(f"{Colors.GREEN}{prefix} Added {added_count} files to vector store.{Colors.RESET}")
    
    if added_count == 0:
        safe_print(f"{Colors.RED}{prefix} Failed to add any files to vector store.{Colors.RESET}")
        project_data["openai_integration"] = {
            "status": "partial",
            "file_ids": file_ids,
            "vector_store": {
                "id": vector_store.id,
                "name": vector_store_name,
                "file_count": 0
            },
            "reason": "no files added to vector store"
        }
        return project_data
    
    # Step 4: Wait for files to be processed
    safe_print(f"{Colors.CYAN}{prefix} Waiting for files to be processed...{Colors.RESET}")
    all_completed = False
    max_checks = OPENAI_PROCESSING_MAX_CHECKS
    check_interval = OPENAI_PROCESSING_CHECK_INTERVAL
    check_count = 0
    
    while not all_completed and check_count < max_checks:
        check_count += 1
        all_completed = check_files_processing_status(client, vector_store.id, prefix)
        
        if not all_completed:
            safe_print(f"{Colors.CYAN}{prefix} Files still processing. Checking again in {check_interval} seconds... (Check {check_count}/{max_checks}){Colors.RESET}")
            time.sleep(check_interval)
    
    # Update project tracking with vector store info
    vector_store_info = {
        "id": vector_store.id,
        "name": vector_store_name,
        "file_count": added_count,
        "processing_completed": all_completed
    }
    
    update_data = {
        "openai_integration": {
            "status": "success",
            "file_ids": file_ids,
            "vector_store": vector_store_info
        }
    }
    
    # Update the project with this info
    update_project_in_tracking(project_id, update_data)
    
    # Add the vector store info to the project data
    project_data["openai_integration"] = {
        "status": "success",
        "file_ids": file_ids,
        "vector_store": vector_store_info
    }
    
    if all_completed:
        safe_print(f"{Colors.BOLD}{Colors.GREEN}{prefix} All files have been processed successfully!{Colors.RESET}")
    else:
        safe_print(f"{Colors.YELLOW}{prefix} Some files are still not processed after maximum wait time. You can check status later.{Colors.RESET}")
    
    return project_data

def main():
    """
    Main entry point for the research orchestrator.
    Implements a three-phase workflow:
    1. Process all research questions to get initial responses
    2. Extract and deduplicate citations from all responses
    3. Process each unique citation once
    4. Consolidate outputs and generate summaries
    5. (Optional) Process files with OpenAI for vector search
    
    Supports three modes:
    - Direct question mode: Provide questions directly via command line or file
    - Topic mode: Generate questions based on a topic, perspective, and depth
    - Interactive mode: Prompt for topic, perspective, and depth when run without arguments
    """
    # Create a unique ID for this research project right at the beginning
    # to ensure it's always available regardless of code path
    project_id = str(uuid.uuid4())
    
    parser = argparse.ArgumentParser(description="Research orchestrator for multiple questions.")
    
    # Create a mutually exclusive group for the two explicit modes
    mode_group = parser.add_mutually_exclusive_group(required=False)  # Changed to False to allow no args
    
    # Direct question mode args
    mode_group.add_argument("--questions", "-q", nargs="+", help="One or more research questions, or a filename containing questions (one per line)")
    
    # Topic mode args
    mode_group.add_argument("--topic", "-t", help="Research topic to generate questions for")
    parser.add_argument("--perspective", "-p", default="Researcher", help="Professional perspective (default: Researcher)")
    parser.add_argument("--depth", "-d", type=int, default=5, help="Number of questions to generate (default: 5)")
    
    # Common args
    parser.add_argument("--output", "-o", default="./research_output", help="Output directory (default: ./research_output)")
    parser.add_argument("--max-workers", "-w", type=int, default=None, help="Maximum number of worker threads (default: automatic based on number of questions)")
    parser.add_argument("--stagger-delay", "-s", type=float, default=None, help="Seconds to wait before starting each new thread (default: from .env)")
    parser.add_argument("--max-citations", "-c", type=int, default=MAX_CITATIONS, help=f"Maximum number of citations to process (default: {MAX_CITATIONS} from .env)")
    
    # OpenAI integration args
    parser.add_argument("--openai-integration", "-ai", choices=["enable", "disable"], help="Enable or disable OpenAI file processing (default: from .env)")
    args = parser.parse_args()
    
    # Override OpenAI integration setting from command line if provided
    if args.openai_integration:
        global ENABLE_OPENAI_INTEGRATION
        ENABLE_OPENAI_INTEGRATION = args.openai_integration == "enable"
    
    # Determine which mode we're operating in
    questions = []
    
    if args.questions:
        # Direct question mode
        if len(args.questions) == 1 and os.path.isfile(args.questions[0]):
            # Questions from file
            with open(args.questions[0], "r", encoding="utf-8") as f:
                questions = [line.strip() for line in f.readlines() if line.strip()]
            safe_print(f"{Colors.GREEN}Loaded {len(questions)} questions from {args.questions[0]}{Colors.RESET}")
        else:
            # Questions from command line
            questions = args.questions
    elif args.topic:
        # Topic mode - generate questions
        safe_print(f"{Colors.BOLD}{Colors.BLUE}Generating questions for topic: {args.topic}{Colors.RESET}")
        questions = generate_research_questions(args.topic, args.perspective, args.depth)
        if not questions:
            safe_print(f"{Colors.RED}Failed to generate questions for topic. Exiting.{Colors.RESET}")
            # Create an empty project tracking entry with failure status
            project_data = {
                "id": project_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "parameters": {
                    "topic": args.topic,
                    "perspective": args.perspective,
                    "depth": args.depth,
                    "questions": []
                },
                "status": "failed",
                "reason": "Failed to generate questions for topic"
            }
            add_project_to_tracking(project_data)
            return project_data
        
        # Limit to the requested depth (in case API returned more)
        questions = questions[:args.depth]
        safe_print(f"{Colors.BOLD}{Colors.GREEN}Generated {len(questions)} questions for topic{Colors.RESET}")
    else:
        # Interactive mode - prompt for inputs
        safe_print(f"{Colors.BOLD}{Colors.BLUE}Welcome to the Research Orchestrator{Colors.RESET}")
        safe_print(f"{Colors.BLUE}Please provide the following information:{Colors.RESET}")
        
        # Prompt for topic
        topic = input(f"{Colors.CYAN}Enter research topic: {Colors.RESET}").strip()
        if not topic:
            safe_print(f"{Colors.RED}Error: Topic is required.{Colors.RESET}")
            return

        # Prompt for perspective
        perspective = input(f"{Colors.CYAN}Enter professional perspective (or press Enter for 'Researcher'): {Colors.RESET}").strip()
        if not perspective:
            perspective = "Researcher"
            safe_print(f"{Colors.YELLOW}Using default perspective: {perspective}{Colors.RESET}")

        # Prompt for depth
        depth_input = input(f"{Colors.CYAN}Enter number of questions to generate (or press Enter for 5): {Colors.RESET}").strip()
        try:
            depth = int(depth_input) if depth_input else 5
            if depth < 1:
                safe_print(f"{Colors.RED}Error: Depth must be at least 1. Using default of 5.{Colors.RESET}")
                depth = 5
            elif depth > 50:
                safe_print(f"{Colors.YELLOW}Warning: Large number of questions requested. This may take a long time.{Colors.RESET}")
        except ValueError:
            safe_print(f"{Colors.RED}Invalid number. Using default of 5.{Colors.RESET}")
            depth = 5
            
        # Prompt for max workers
        workers_input = input(f"{Colors.CYAN}Enter maximum number of worker threads (or press Enter for automatic): {Colors.RESET}").strip()
        if workers_input:
            try:
                args.max_workers = int(workers_input)
                if args.max_workers < 1:
                    safe_print(f"{Colors.RED}Error: Workers must be at least 1. Using automatic calculation.{Colors.RESET}")
                    args.max_workers = None
            except ValueError:
                safe_print(f"{Colors.RED}Invalid number. Using automatic calculation.{Colors.RESET}")
        
        # Prompt for max citations
        citations_input = input(f"{Colors.CYAN}Enter maximum number of citations to process (or press Enter for 50): {Colors.RESET}").strip()
        if citations_input:
            try:
                args.max_citations = int(citations_input)
                if args.max_citations < 1:
                    safe_print(f"{Colors.RED}Error: Max citations must be at least 1. Using default of 50.{Colors.RESET}")
                    args.max_citations = 50
            except ValueError:
                safe_print(f"{Colors.RED}Invalid number. Using default of 50.{Colors.RESET}")
        
        # Generate questions using the provided inputs
        safe_print(f"{Colors.BOLD}{Colors.BLUE}Generating questions for topic: {topic}{Colors.RESET}")
        questions = generate_research_questions(topic, perspective, depth)
        if not questions:
            safe_print(f"{Colors.RED}Failed to generate questions for topic. Exiting.{Colors.RESET}")
            # Create an empty project tracking entry with failure status
            project_data = {
                "id": project_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "parameters": {
                    "topic": topic,
                    "perspective": perspective,
                    "depth": depth,
                    "questions": []
                },
                "status": "failed",
                "reason": "Failed to generate questions for topic"
            }
            add_project_to_tracking(project_data)
            return project_data
        
        # Limit to the requested depth (in case API returned more)
        questions = questions[:depth]
        safe_print(f"{Colors.BOLD}{Colors.GREEN}Generated {len(questions)} questions for topic{Colors.RESET}")
        
        # Save the values to args for later use
        args.topic = topic
        args.perspective = perspective
        args.depth = depth

    if not questions:
        safe_print(f"{Colors.RED}No research questions provided. Exiting.{Colors.RESET}")
        # Create an empty project tracking entry with failure status
        project_data = {
            "id": project_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "parameters": {
                "questions": []
            },
            "status": "failed",
            "reason": "No research questions provided"
        }
        # Add topic, perspective, and depth if available
        if args.topic:
            project_data["parameters"]["topic"] = args.topic
            project_data["parameters"]["perspective"] = args.perspective
            project_data["parameters"]["depth"] = args.depth
        
        add_project_to_tracking(project_data)
        return project_data

    # Load the rate limit settings
    RATE_LIMIT_QUESTIONS_PER_WORKER = int(os.getenv('RATE_LIMIT_QUESTIONS_PER_WORKER', 10))
    THREAD_STAGGER_DELAY = float(os.getenv('THREAD_STAGGER_DELAY', 5.0))
    
    # Override with command line args if provided
    if args.stagger_delay is not None:
        THREAD_STAGGER_DELAY = args.stagger_delay
        
    # Calculate max workers based on number of questions and rate limit
    if args.max_workers is not None:
        max_workers = args.max_workers
    else:
        max_workers = max(1, int(len(questions) / RATE_LIMIT_QUESTIONS_PER_WORKER))
    
    # Create a descriptive folder name (with topic if available)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    folder_prefix = "research"
    if args.topic:
        # Sanitize topic for folder name
        safe_topic = re.sub(r'[^\w\s-]', '', args.topic)
        safe_topic = re.sub(r'[-\s]+', '_', safe_topic).strip('-_')
        safe_topic = safe_topic[:30] if len(safe_topic) > 30 else safe_topic
        folder_prefix = safe_topic
    
    master_folder = os.path.join(os.path.abspath(args.output), f"{folder_prefix}_{timestamp}")
    
    # Create all required directories
    os.makedirs(master_folder, exist_ok=True)
    os.makedirs(os.path.join(master_folder, "response"), exist_ok=True)
    os.makedirs(os.path.join(master_folder, "markdown"), exist_ok=True)
    os.makedirs(os.path.join(master_folder, "summaries"), exist_ok=True)  # Added new summaries directory
    
    # Create a README for the project
    readme_path = os.path.join(master_folder, "README.md")
    topic_title = args.topic if args.topic else "Research Project"

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(f"# {topic_title}\n\n")
        f.write(f"**Generated on**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n\n")
        f.write(f"**Project ID**: {project_id}\n\n")

        if args.topic:
            f.write(f"**Topic**: {args.topic}\n\n")
            f.write(f"**Perspective**: {args.perspective}\n\n")
        
        f.write("## Folder Structure\n\n")
        f.write("- `markdown/`: Formatted markdown files for each research question\n")
        f.write("- `response/`: Raw API responses\n")
        f.write("- `summaries/`: Consolidated files and indexes\n\n")
        
        # Add OpenAI integration info if enabled
        if ENABLE_OPENAI_INTEGRATION:
            f.write("## OpenAI Integration\n\n")
            f.write("This research project has been integrated with OpenAI's file search capabilities:\n\n")
            f.write("- Research files have been uploaded to OpenAI\n")
            f.write("- A vector store has been created for semantic search\n")
            f.write("- Project tracking information is stored in the research_projects.json file\n\n")
            f.write("Use the project ID above when using the OpenAI search functionality.\n\n")
        
        f.write("## Research Questions\n\n")
        for i, q in enumerate(questions, 1):
            f.write(f"{i}. {q}\n")
    
    safe_print(f"{Colors.BOLD}{Colors.GREEN}Research orchestrator started at {time.strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}")
    safe_print(f"{Colors.MAGENTA}Processing {len(questions)} questions with a maximum of {max_workers} worker threads{Colors.RESET}")
    safe_print(f"{Colors.MAGENTA}Thread stagger delay: {THREAD_STAGGER_DELAY} seconds{Colors.RESET}")
    safe_print(f"{Colors.MAGENTA}Max citations to process: {args.max_citations} (prioritizing most referenced ones){Colors.RESET}")
    safe_print(f"{Colors.MAGENTA}Output directory: {master_folder}{Colors.RESET}")
    
    # Create a project data structure for tracking
    project_data = {
        "id": project_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "parameters": {
            "questions": questions
        },
        "local_storage": {
            "folder": os.path.abspath(master_folder),
            "markdown_folder": "markdown",
            "summary_folder": "summaries",
            "response_folder": "response"
        },
        "status": "in_progress"
    }
    
    # Add topic, perspective, and depth if available
    if args.topic:
        project_data["parameters"]["topic"] = args.topic
        project_data["parameters"]["perspective"] = args.perspective
        project_data["parameters"]["depth"] = args.depth
    
    # Add project to tracking file
    add_project_to_tracking(project_data)
    
    ########## PHASE 1: Process all questions ##########
    safe_print(f"\n{Colors.BOLD}{Colors.CYAN}======== PHASE 1: PROCESSING ALL QUESTIONS ========{Colors.RESET}")
    
    # Process each question with ThreadPoolExecutor
    all_question_results = []  # Store results as (success_flag, research_response, citations)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a future for each question
        futures = []
        for i, question in enumerate(questions, 1):
            # Stagger the thread starts to avoid API rate limits
            if i > 1 and THREAD_STAGGER_DELAY > 0:
                time.sleep(THREAD_STAGGER_DELAY)
                
            # Pass topic and perspective if available
            if args.topic:
                future = executor.submit(research_pipeline, question, master_folder, i, len(questions), args.topic, args.perspective)
            else:
                future = executor.submit(research_pipeline, question, master_folder, i, len(questions))
                
            futures.append(future)
            
        # Collect results as they complete
        for future in futures:
            result = future.result()  # This blocks until the result is available
            all_question_results.append(result)
            
    # Count successful questions
    successful_questions = sum(1 for success, _, _ in all_question_results if success)
    safe_print(f"\n{Colors.BOLD}{Colors.GREEN}Phase 1 complete: Successfully processed {successful_questions} out of {len(questions)} questions.{Colors.RESET}")
    
    ########## PHASE 2: Extract and deduplicate citations ##########
    safe_print(f"\n{Colors.BOLD}{Colors.CYAN}======== PHASE 2: EXTRACTING AND DEDUPLICATING CITATIONS ========{Colors.RESET}")
    
    # Extract and deduplicate citations
    citation_map = extract_and_deduplicate_citations(all_question_results)
    unique_citation_count = len(citation_map)
    total_citation_references = sum(len(questions) for questions in citation_map.values())
    
    safe_print(f"{Colors.GREEN}Found {unique_citation_count} unique citations across {successful_questions} questions.{Colors.RESET}")
    safe_print(f"{Colors.GREEN}Total citation references: {total_citation_references} (avg {total_citation_references/max(1, successful_questions):.1f} per question){Colors.RESET}")
    
    # Prioritize citations - limit to the top N most referenced ones
    prioritized_citation_map, skipped_count = prioritize_citations(citation_map, args.max_citations)
    
    if skipped_count > 0:
        safe_print(f"{Colors.YELLOW}Limiting to top {args.max_citations} most referenced citations. Skipping {skipped_count} less referenced citations.{Colors.RESET}")
    
    if len(prioritized_citation_map) == 0:
        safe_print(f"{Colors.YELLOW}No citations found. Skipping citation processing phase.{Colors.RESET}")
        # Create the master index even if there are no citations
        master_index_path = create_master_index(master_folder, questions, all_question_results)
        
        # Consolidate summary files and move master index
        safe_print(f"\n{Colors.BOLD}{Colors.CYAN}======== PHASE 4: CONSOLIDATING SUMMARIES ========{Colors.RESET}")
        consolidate_summary_files(master_folder, "executive_summary", "consolidated_executive_summaries.md", "Consolidated Executive Summaries")
        consolidate_summary_files(master_folder, "research_summary", "consolidated_research_summaries.md", "Consolidated Research Summaries")
        move_file(master_index_path, os.path.join(master_folder, "summaries"))
        return project_data
        
    ########## PHASE 3: Process each unique citation ##########
    safe_print(f"\n{Colors.BOLD}{Colors.CYAN}======== PHASE 3: PROCESSING UNIQUE CITATIONS ========{Colors.RESET}")
    safe_print(f"{Colors.CYAN}Processing {len(prioritized_citation_map)} prioritized citations...{Colors.RESET}")
    
    # Process citations in parallel
    citation_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a future for each unique citation
        futures = {}
        for i, (citation_url, question_context) in enumerate(prioritized_citation_map.items(), 1):
            # Stagger the thread starts to avoid API rate limits
            if i > 1 and THREAD_STAGGER_DELAY > 0:
                time.sleep(THREAD_STAGGER_DELAY)
                
            # For log message, show citation rank by reference count
            ref_count = len(question_context)
            
            # Use the timeout wrapper with a configurable timeout
            # This ensures no single citation can hang the entire process
            future = executor.submit(
                with_timeout,
                process_citation, 
                citation_url,
                question_context,
                master_folder,
                i,
                len(prioritized_citation_map),
                f"[Refs: {ref_count}]",
                CITATION_TIMEOUT  # Use timeout from environment
            )
            futures[future] = (i, citation_url, ref_count)
            
        # Collect results as they complete
        for future in as_completed(futures):
            i, citation_url, ref_count = futures[future]
            try:
                result = future.result()
                citation_results.append(result)
                success_indicator = Colors.GREEN + "✓" if result["success"] else Colors.RED + "✗"
                safe_print(f"{success_indicator} Citation {i}/{len(prioritized_citation_map)} complete: {citation_url[:60]}... (Referenced by {ref_count} questions){Colors.RESET}")
            except Exception as e:
                safe_print(f"{Colors.RED}Error processing citation {i}/{len(prioritized_citation_map)}: {str(e)}{Colors.RESET}")
                citation_results.append({
                    "citation_id": i,
                    "url": citation_url,
                    "success": False,
                    "content": f"# Error Processing Citation\n\n**Error**: {str(e)}",
                    "error": str(e)
                })
    
    # Count successful citations
    successful_citations = sum(1 for result in citation_results if result["success"])
    safe_print(f"\n{Colors.BOLD}{Colors.GREEN}Phase 3 complete: Successfully processed {successful_citations} out of {len(prioritized_citation_map)} citations.{Colors.RESET}")
    
    # Create indexes - pass the original citation_map to create_citation_index
    # so it can show all citations (including skipped ones)
    master_index_path = create_master_index(master_folder, questions, all_question_results)
    citation_index_path = create_citation_index(master_folder, citation_map, citation_results, skipped_count)
    
    ########## PHASE 4: Consolidate summaries ##########
    safe_print(f"\n{Colors.BOLD}{Colors.CYAN}======== PHASE 4: CONSOLIDATING SUMMARIES ========{Colors.RESET}")
    
    # Consolidate executive summaries and research summaries
    exec_summary_path = consolidate_summary_files(master_folder, "executive_summary", "consolidated_executive_summaries.md", "Consolidated Executive Summaries")
    research_summary_path = consolidate_summary_files(master_folder, "research_summary", "consolidated_research_summaries.md", "Consolidated Research Summaries")
    
    # Move master index and citation index to summaries folder
    move_file(master_index_path, os.path.join(master_folder, "summaries"))
    move_file(citation_index_path, os.path.join(master_folder, "summaries"))
    
    # Update README to mention consolidated files
    if args.topic and os.path.exists(readme_path):
        with open(readme_path, "a", encoding="utf-8") as f:
            f.write("\n## Consolidated Files\n\n")
            f.write("For convenience, the following consolidated files are available in the `summaries/` directory:\n\n")
            f.write("- `consolidated_executive_summaries.md`: All executive summaries in one file\n")
            f.write("- `consolidated_research_summaries.md`: All research summaries in one file\n")
            f.write("- `master_index.md`: Index of all questions and their research outputs\n")
            f.write("- `citation_index.md`: Index of all citations and their references\n")
    
    ########## PHASE 5: OpenAI File Processing (Optional) ##########
    # Only run if OpenAI integration is enabled
    if ENABLE_OPENAI_INTEGRATION:
        project_data = process_files_with_openai(master_folder, project_data)
    
    # Output summary
    safe_print(f"\n{Colors.BOLD}{Colors.GREEN}Research complete at {time.strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}")
    safe_print(f"{Colors.CYAN}Summary:{Colors.RESET}")
    safe_print(f"{Colors.CYAN}- Questions: {successful_questions}/{len(questions)} successfully processed{Colors.RESET}")
    safe_print(f"{Colors.CYAN}- Citations: Found {unique_citation_count} unique citations{Colors.RESET}")
    if skipped_count > 0:
        safe_print(f"{Colors.CYAN}- Citation Processing: {successful_citations}/{len(prioritized_citation_map)} prioritized citations processed, {skipped_count} less relevant citations skipped{Colors.RESET}")
    else:
        safe_print(f"{Colors.CYAN}- Citation Processing: {successful_citations}/{len(prioritized_citation_map)} citations processed{Colors.RESET}")
    safe_print(f"{Colors.CYAN}- Consolidated Files: Executive summaries, research summaries{Colors.RESET}")
    safe_print(f"{Colors.CYAN}- Output directory: {master_folder}{Colors.RESET}")
    safe_print(f"{Colors.CYAN}- Summaries directory: {os.path.join(master_folder, 'summaries')}{Colors.RESET}")
    
    # Add OpenAI info to the summary if it was processed
    if project_data.get("openai_integration"):
        vs_info = project_data["openai_integration"].get("vector_store", {})
        file_ids = project_data["openai_integration"].get("file_ids", {})
        
        total_files = (1 if file_ids.get("readme") else 0) + len(file_ids.get("markdown_files", [])) + len(file_ids.get("summary_files", []))
        
        safe_print(f"{Colors.CYAN}- OpenAI Integration: {total_files} files uploaded{Colors.RESET}")
        safe_print(f"{Colors.CYAN}- Vector Store: {vs_info.get('name')} (ID: {vs_info.get('id')}){Colors.RESET}")
    
    # Update project status to completed
    project_data["status"] = "completed"
    update_project_in_tracking(project_id, {"status": "completed"})
    
    return project_data

if __name__ == "__main__":
    main() 
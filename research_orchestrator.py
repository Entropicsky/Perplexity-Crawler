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

Usage (Topic Mode):
    python research_orchestrator.py --topic "Kahua, the Construction Software Management Company" --perspective "Chief Product Officer" --depth 5

Usage (Direct Question Mode):
    python research_orchestrator.py --questions "What is quantum computing?" "How do quantum computers work?"
    
Usage (Questions from File):
    python research_orchestrator.py --questions questions.txt

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

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

def research_pipeline(question, master_folder, question_number, total_questions):
    """
    Phase 1 of the research process: Get initial research response for a question.
    Modified to stop after getting the research response - does not process citations.
    
    Returns a tuple of (success_flag, research_response, citations)
    """
    safe_print(f"\n{Colors.BOLD}{Colors.MAGENTA}[{question_number}/{total_questions}] Researching: '{question}'{Colors.RESET}")
    
    try:
        # Set up paths for this question
        safe_question = re.sub(r'[^\w\s-]', '', question)
        safe_question = re.sub(r'[-\s]+', '_', safe_question).strip('-_')
        safe_question = safe_question[:30] if len(safe_question) > 30 else safe_question
        
        question_prefix = f"Q{question_number:02d}_"
        prefix = f"[Q{question_number}]"  # For log messages
        
        # Timestamp not needed for individual questions as they're in the master folder
        response_dir = os.path.join(master_folder, "response")
        markdown_dir = os.path.join(master_folder, "markdown")
        
        # STEP 1: Call Perplexity with 'research' model for the initial research
        safe_print(f"{Colors.CYAN}{prefix} Starting research...{Colors.RESET}")
        research_response = with_retry(
            query_perplexity,
            prompt=question,
            model=PERPLEXITY_RESEARCH_MODEL,
            system_prompt="Perform thorough research on the user's query.",
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
            f.write(main_content)
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
        
        # Save the executive summary separately
        exec_summary_path = os.path.join(markdown_dir, f"{question_prefix}executive_summary.md")
        with open(exec_summary_path, "w", encoding="utf-8") as f:
            f.write(f"# Executive Summary: {question}\n\n")
            f.write(exec_summary)
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
            f.write(f"{i}. {status} [Q{i:02d}: {question[:80]}{'...' if len(question) > 80 else ''}](#q{i:02d})\n")
        
        f.write("\n---\n\n")
        
        # Question details
        for i, question in enumerate(questions, 1):
            f.write(f"## Q{i:02d}\n\n")
            success, research_response, citations = results[i-1] if i-1 < len(results) else (False, None, [])
            
            f.write(f"**Question**: {question}\n\n")
            
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
                
            # Links to outputs
            question_prefix = f"Q{i:02d}_"
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
            else:
                cleaned_content = "Error: Failed to get cleaned content from Perplexity."
        
        # Save the cleaned markdown
        md_filename = os.path.join(markdown_dir, f"{citation_prefix}citation.md")
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(f"# Citation {citation_id}: {citation_url}\n\n")
            f.write(f"## Referenced by\n\n{questions_summary}\n\n")
            f.write("## Content\n\n")
            f.write(cleaned_content)
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

def create_citation_index(master_folder, citation_map, citation_results):
    """
    Create an index of all citations in Markdown format.
    
    Args:
        master_folder: The master folder for output
        citation_map: Mapping of citations to questions
        citation_results: Results from processing citations
    """
    markdown_dir = os.path.join(master_folder, "markdown")
    citation_index_path = os.path.join(markdown_dir, "citation_index.md")
    
    with open(citation_index_path, "w", encoding="utf-8") as f:
        f.write("# Citation Index\n\n")
        f.write("This document indexes all unique citations found during research.\n\n")
        
        # Create a table of contents
        f.write("## Table of Contents\n\n")
        for i, citation_result in enumerate(citation_results, 1):
            citation_url = citation_result["url"]
            success_mark = "✅" if citation_result["success"] else "❌"
            f.write(f"{i}. {success_mark} [Citation {i}: {citation_url[:60]}...](#citation-{i})\n")
        
        f.write("\n---\n\n")
        
        # Add detailed entries for each citation
        for i, citation_result in enumerate(citation_results, 1):
            citation_url = citation_result["url"]
            success_status = "Successfully processed" if citation_result["success"] else "Processing failed"
            
            f.write(f"## Citation {i}\n\n")
            f.write(f"**URL**: [{citation_url}]({citation_url})\n\n")
            f.write(f"**Status**: {success_status}\n\n")
            
            # List referencing questions
            questions = citation_map.get(citation_url, [])
            f.write("**Referenced by**:\n\n")
            for q in questions:
                f.write(f"- Q{q['question_number']:02d}: {q['question']}\n")
            
            f.write("\n---\n\n")
    
    safe_print(f"{Colors.GREEN}Created citation index with {len(citation_results)} citations.{Colors.RESET}")
    return citation_index_path

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

def main():
    """
    Main entry point for the research orchestrator.
    Implements a three-phase workflow:
    1. Process all research questions to get initial responses
    2. Extract and deduplicate citations from all responses
    3. Process each unique citation once
    
    Supports three modes:
    - Direct question mode: Provide questions directly via command line or file
    - Topic mode: Generate questions based on a topic, perspective, and depth
    - Interactive mode: Prompt for topic, perspective, and depth when run without arguments
    """
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
    args = parser.parse_args()
    
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
            return
        
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
        
        # Generate questions using the provided inputs
        safe_print(f"{Colors.BOLD}{Colors.BLUE}Generating questions for topic: {topic}{Colors.RESET}")
        questions = generate_research_questions(topic, perspective, depth)
        if not questions:
            safe_print(f"{Colors.RED}Failed to generate questions for topic. Exiting.{Colors.RESET}")
            return
        
        # Save the values to args for later use
        args.topic = topic
        args.perspective = perspective
        args.depth = depth
        
        # Limit to the requested depth (in case API returned more)
        questions = questions[:depth]
        safe_print(f"{Colors.BOLD}{Colors.GREEN}Generated {len(questions)} questions for topic{Colors.RESET}")
        
    if not questions:
        safe_print(f"{Colors.RED}No research questions provided. Exiting.{Colors.RESET}")
        return

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
    
    # Create a README for the project
    if args.topic:
        readme_path = os.path.join(master_folder, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(f"# Research Project: {args.topic}\n\n")
            f.write(f"**Generated on**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n\n")
            f.write(f"**Topic**: {args.topic}\n\n")
            f.write(f"**Perspective**: {args.perspective}\n\n")
            f.write("## Folder Structure\n\n")
            f.write("- `markdown/`: Formatted markdown files for each research question\n")
            f.write("- `response/`: Raw API responses\n\n")
            f.write("## Research Questions\n\n")
            for i, q in enumerate(questions, 1):
                f.write(f"{i}. {q}\n")
    
    safe_print(f"{Colors.BOLD}{Colors.GREEN}Research orchestrator started at {time.strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}")
    safe_print(f"{Colors.MAGENTA}Processing {len(questions)} questions with a maximum of {max_workers} worker threads{Colors.RESET}")
    safe_print(f"{Colors.MAGENTA}Thread stagger delay: {THREAD_STAGGER_DELAY} seconds{Colors.RESET}")
    safe_print(f"{Colors.MAGENTA}Output directory: {master_folder}{Colors.RESET}")
    
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
    
    if unique_citation_count == 0:
        safe_print(f"{Colors.YELLOW}No citations found. Skipping citation processing phase.{Colors.RESET}")
        # Create the master index even if there are no citations
        create_master_index(master_folder, questions, all_question_results)
        return
        
    ########## PHASE 3: Process each unique citation ##########
    safe_print(f"\n{Colors.BOLD}{Colors.CYAN}======== PHASE 3: PROCESSING UNIQUE CITATIONS ========{Colors.RESET}")
    
    # Process citations in parallel
    citation_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a future for each unique citation
        futures = {}
        for i, (citation_url, question_context) in enumerate(citation_map.items(), 1):
            # Stagger the thread starts to avoid API rate limits
            if i > 1 and THREAD_STAGGER_DELAY > 0:
                time.sleep(THREAD_STAGGER_DELAY)
                
            future = executor.submit(
                process_citation, 
                citation_url, 
                question_context, 
                master_folder, 
                i, 
                unique_citation_count
            )
            futures[future] = (i, citation_url)
            
        # Collect results as they complete
        for future in as_completed(futures):
            i, citation_url = futures[future]
            try:
                result = future.result()
                citation_results.append(result)
                success_indicator = Colors.GREEN + "✓" if result["success"] else Colors.RED + "✗"
                safe_print(f"{success_indicator} Citation {i}/{unique_citation_count} complete: {citation_url[:60]}...{Colors.RESET}")
            except Exception as e:
                safe_print(f"{Colors.RED}Error processing citation {i}/{unique_citation_count}: {str(e)}{Colors.RESET}")
                citation_results.append({
                    "citation_id": i,
                    "url": citation_url,
                    "success": False,
                    "content": f"# Error Processing Citation\n\n**Error**: {str(e)}",
                    "error": str(e)
                })
    
    # Count successful citations
    successful_citations = sum(1 for result in citation_results if result["success"])
    safe_print(f"\n{Colors.BOLD}{Colors.GREEN}Phase 3 complete: Successfully processed {successful_citations} out of {unique_citation_count} citations.{Colors.RESET}")
    
    # Create indexes
    create_master_index(master_folder, questions, all_question_results)
    create_citation_index(master_folder, citation_map, citation_results)
    
    # Output summary
    safe_print(f"\n{Colors.BOLD}{Colors.GREEN}Research complete at {time.strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}")
    safe_print(f"{Colors.CYAN}Summary:{Colors.RESET}")
    safe_print(f"{Colors.CYAN}- Questions: {successful_questions}/{len(questions)} successfully processed{Colors.RESET}")
    safe_print(f"{Colors.CYAN}- Citations: {successful_citations}/{unique_citation_count} successfully processed{Colors.RESET}")
    safe_print(f"{Colors.CYAN}- Output directory: {master_folder}{Colors.RESET}")

if __name__ == "__main__":
    main() 
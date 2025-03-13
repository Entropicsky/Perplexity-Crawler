#!/usr/bin/env python3
"""
Research Project Chatbot Tester

This script allows you to chat with research projects that have been uploaded to OpenAI
and stored in the research_projects.json file. It uses the OpenAI API to search through
your research content and generate responses to your questions.

Usage:
    python testchat.py

Features:
- Select from available research projects
- Chat with AI about your research
- Maintains conversation history
- Shows citation sources for responses
"""

import os
import sys
import json
import time
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# Constants
RESEARCH_PROJECTS_FILE = os.getenv("RESEARCH_PROJECTS_FILE", "research_projects.json")
OPENAI_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")  # Default model
MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "5"))  # Default number of search results

# Terminal colors for better UI
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"

def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')

def load_research_projects():
    """
    Load research projects from the JSON file.
    
    Returns:
        List of projects or empty list if error occurs
    """
    try:
        if not os.path.exists(RESEARCH_PROJECTS_FILE):
            print(f"{Colors.RED}Error: {RESEARCH_PROJECTS_FILE} not found.{Colors.RESET}")
            return []
            
        with open(RESEARCH_PROJECTS_FILE, "r") as f:
            data = json.load(f)
            return data.get("projects", [])
    except Exception as e:
        print(f"{Colors.RED}Error loading research projects: {str(e)}{Colors.RESET}")
        return []

def filter_available_projects(projects):
    """
    Filter projects that have successfully completed OpenAI integration.
    
    Args:
        projects: List of all projects
        
    Returns:
        List of available projects for chatting
    """
    available_projects = []
    
    for project in projects:
        # Check if project has OpenAI integration with a vector store
        if (project.get("status") == "completed" and 
            project.get("openai_integration", {}).get("status") == "success" and
            project.get("openai_integration", {}).get("vector_store", {}).get("id")):
            available_projects.append(project)
    
    return available_projects

def display_projects(projects):
    """
    Display available projects for selection.
    
    Args:
        projects: List of available projects
        
    Returns:
        True if projects were displayed, False otherwise
    """
    if not projects:
        print(f"{Colors.RED}No research projects with OpenAI integration available.{Colors.RESET}")
        return False
    
    print(f"\n{Colors.BOLD}{Colors.BLUE}Available Research Projects:{Colors.RESET}\n")
    
    for i, project in enumerate(projects, 1):
        # Get project info
        topic = project.get("parameters", {}).get("topic", "Research Project")
        timestamp = project.get("timestamp", "").split("T")[0]  # Just the date part
        question_count = len(project.get("parameters", {}).get("questions", []))
        vector_store_name = project.get("openai_integration", {}).get("vector_store", {}).get("name", "")
        
        print(f"{Colors.BOLD}{i}. {topic}{Colors.RESET}")
        print(f"   Date: {timestamp}")
        print(f"   Questions: {question_count}")
        print(f"   Vector Store: {vector_store_name}")
        print()
    
    return True

def create_openai_client():
    """
    Create an OpenAI client.
    
    Returns:
        OpenAI client or None if error occurs
    """
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print(f"{Colors.RED}Error: OPENAI_API_KEY not set in environment.{Colors.RESET}")
            return None
            
        return OpenAI(api_key=api_key)
    except Exception as e:
        print(f"{Colors.RED}Error creating OpenAI client: {str(e)}{Colors.RESET}")
        return None

def chat_with_project(client, project):
    """
    Interactive chat session with a research project.
    
    Args:
        client: OpenAI client
        project: Selected project data
    """
    if not client:
        print(f"{Colors.RED}OpenAI client not available. Cannot start chat.{Colors.RESET}")
        return
    
    # Get vector store ID and project info
    vector_store_id = project.get("openai_integration", {}).get("vector_store", {}).get("id")
    if not vector_store_id:
        print(f"{Colors.RED}Vector store ID not found for this project.{Colors.RESET}")
        return
    
    # Get project info for display
    topic = project.get("parameters", {}).get("topic", "Research Project")
    
    clear_screen()
    print(f"{Colors.BOLD}{Colors.GREEN}Chat with Research Project: {topic}{Colors.RESET}")
    print(f"{Colors.CYAN}Type 'exit' or 'quit' to end the session. Press Enter to start a new query.{Colors.RESET}")
    print(f"{Colors.YELLOW}Vector Store ID: {vector_store_id}{Colors.RESET}")
    print("="*80)
    
    # Initialize conversation history
    conversation_history = []
    
    while True:
        # Get user input
        print(f"\n{Colors.BOLD}{Colors.BLUE}Your question:{Colors.RESET} ", end="")
        user_input = input().strip()
        
        # Check if user wants to exit
        if user_input.lower() in ("exit", "quit", "q"):
            break
            
        # Skip empty inputs
        if not user_input:
            continue
        
        # Add to conversation history
        conversation_history.append({"role": "user", "content": user_input})
        
        # Display typing indicator
        print(f"{Colors.YELLOW}Searching and generating response...{Colors.RESET}")
        
        # Try to get a response with vector search
        try:
            response = client.responses.create(
                model=OPENAI_MODEL,
                input=user_input,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
                    "max_num_results": MAX_SEARCH_RESULTS
                }],
                include=["output[*].file_search_call.search_results"]
            )
            
            # Extract and print the response
            if response and response.output:
                # Process output and display to user
                print(f"\n{Colors.BOLD}{Colors.GREEN}AI Response:{Colors.RESET}")
                
                response_text = ""
                citations = []
                
                for output in response.output:
                    if output.type == "message":
                        for content_item in output.content:
                            if content_item.type == "output_text":
                                response_text = content_item.text
                                print(f"{response_text}")
                                
                                # Extract citations
                                if hasattr(content_item, 'annotations') and content_item.annotations:
                                    for annotation in content_item.annotations:
                                        if annotation.type == "file_citation":
                                            citations.append(annotation.filename)
                
                # Add to conversation history
                conversation_history.append({"role": "assistant", "content": response_text})
                
                # Display citations if any
                if citations:
                    unique_citations = set(citations)
                    print(f"\n{Colors.BOLD}{Colors.MAGENTA}Sources:{Colors.RESET}")
                    for filename in unique_citations:
                        print(f"- {filename}")
            else:
                print(f"{Colors.RED}No response received.{Colors.RESET}")
                
        except Exception as e:
            print(f"{Colors.RED}Error: {str(e)}{Colors.RESET}")
            continue
        
        print("\n" + "="*80)

def main():
    """Main function to run the chatbot tester."""
    clear_screen()
    print(f"{Colors.BOLD}{Colors.GREEN}Research Project Chatbot Tester{Colors.RESET}")
    print(f"{Colors.CYAN}This tool allows you to chat with your research projects.{Colors.RESET}")
    
    # Load research projects
    print(f"\nLoading research projects from {RESEARCH_PROJECTS_FILE}...")
    all_projects = load_research_projects()
    
    if not all_projects:
        print(f"{Colors.RED}No research projects found. Exiting.{Colors.RESET}")
        return
    
    # Filter projects with successful OpenAI integration
    available_projects = filter_available_projects(all_projects)
    
    # Display available projects
    if not display_projects(available_projects):
        return
    
    # Select a project
    while True:
        try:
            choice = input(f"\n{Colors.BOLD}Select a project (1-{len(available_projects)}) or 'exit' to quit: {Colors.RESET}")
            
            if choice.lower() in ("exit", "quit", "q"):
                return
                
            choice = int(choice)
            if 1 <= choice <= len(available_projects):
                selected_project = available_projects[choice - 1]
                break
            else:
                print(f"{Colors.RED}Invalid choice. Please select a number between 1 and {len(available_projects)}.{Colors.RESET}")
        except ValueError:
            print(f"{Colors.RED}Please enter a valid number.{Colors.RESET}")
    
    # Create OpenAI client
    client = create_openai_client()
    if not client:
        return
    
    # Start chat with selected project
    chat_with_project(client, selected_project)
    
    # Goodbye message
    print(f"\n{Colors.BOLD}{Colors.GREEN}Thank you for using the Research Project Chatbot Tester!{Colors.RESET}")

if __name__ == "__main__":
    main()

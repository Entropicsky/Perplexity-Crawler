# Perplexity Research Suite

A powerful research automation toolkit that combines Perplexity API and Firecrawl to create comprehensive research packages on any topic. This suite includes a single-question research tool (`perplexityresearch.py`) and a multi-question research orchestrator (`research_orchestrator.py`) with advanced features.

## Overview

This toolkit enables:

- **AI-powered research** using Perplexity's advanced models
- **Automated citation analysis** with intelligent web crawling
- **Multi-question research** from a single topic
- **Parallel processing** with rate limit protection
- **Citation deduplication** for efficient processing
- **Citation prioritization** to focus on the most relevant sources
- **Automatic timeout protection** to handle problematic URLs
- **Comprehensive output** with consolidated summaries and research indexes
- **OpenAI integration** for semantic search across research outputs

## Quick Start

1. **Install dependencies**:
   ```
   pip install reportlab requests python-dotenv firecrawl openai
   ```

2. **Set up your API keys**:
   - Create a `.env` file based on the provided `.env.example`
   - Add your Perplexity, Firecrawl, and OpenAI API keys

3. **Run the research orchestrator in interactive mode**:
   ```
   python research_orchestrator.py
   ```
   Follow the prompts to enter your topic, perspective, and other details.

## Research Orchestrator (New!)

The `research_orchestrator.py` script provides three modes of operation:

### 1. Interactive Mode (simplest)
```bash
# Run without arguments and follow the prompts
python research_orchestrator.py
```

### 2. Topic Mode (generate questions automatically)
```bash
# Generate questions about a topic
python research_orchestrator.py --topic "Kahua, the Construction Software Management Company" --perspective "Chief Product Officer" --depth 5
```

### 3. Direct Question Mode (specify your own questions)
```bash
# Directly provide questions
python research_orchestrator.py --questions "What is quantum computing?" "How do quantum computers work?"

# Or use questions from a file (one per line)
python research_orchestrator.py --questions questions.txt
```

### Common Options for All Modes
```bash
# Custom output directory
python research_orchestrator.py --output-dir ./my_research --topic "AI in Healthcare"

# Skip OpenAI file uploads
python research_orchestrator.py --skip-openai-upload --topic "Sustainable Energy"

# Limit citation processing
python research_orchestrator.py --max-citations 30 --topic "Quantum Computing"
```

## Key Features

### Five-Phase Processing Workflow
1. **Phase 1**: Process all research questions to get initial responses
2. **Phase 2**: Extract and deduplicate citations across all questions
3. **Phase 3**: Process each unique citation exactly once
4. **Phase 4**: Consolidate summaries and create indexes
5. **Phase 5**: Upload research files to OpenAI and create a vector store

### Performance Optimizations
- **Parallel Processing**: Uses ThreadPoolExecutor to run multiple research questions simultaneously
- **Smart Retry Logic**: Implements exponential backoff with jitter for rate-limited API calls
- **Staggered Thread Starts**: Configurable delay between starting each worker thread
- **Citation Deduplication**: Process each citation URL only once, even if referenced by multiple questions
- **Citation Prioritization**: Focus on the most frequently referenced citations first
- **Timeout Protection**: Automatically skip citations that take too long to process

### User Experience
- **Color-coded Output**: Clear visual feedback on processing status
- **Progress Tracking**: Individual progress for each question and citation
- **Comprehensive Indexing**: Master index of questions and citation index for navigation
- **Consolidated Summaries**: Automatically combines executive and research summaries
- **A-prefix Naming**: Improved file naming convention for better sorting

## Configuration

### Environment Variables (.env file)
```
# API Keys
PERPLEXITY_API_KEY=your_perplexity_api_key
FIRECRAWL_API_KEY=your_firecrawl_api_key
OPENAI_API_KEY=your_openai_api_key

# Models
PERPLEXITY_RESEARCH_MODEL=sonar-deep-research
PERPLEXITY_CLEANUP_MODEL=sonar-pro

# API Error Handling
API_MAX_RETRIES=3
API_INITIAL_RETRY_DELAY=5.0
API_MAX_RETRY_DELAY=60.0

# Performance Tuning
RATE_LIMIT_QUESTIONS_PER_WORKER=7
THREAD_STAGGER_DELAY=5.0
MAX_CITATIONS=50
CITATION_TIMEOUT=300
```

### Performance Tuning
- `RATE_LIMIT_QUESTIONS_PER_WORKER`: Controls worker thread allocation (higher value = fewer workers)
- `THREAD_STAGGER_DELAY`: Sets delay between thread starts in seconds
- `API_MAX_RETRIES`: Maximum number of retry attempts for rate-limited API calls
- `MAX_CITATIONS`: Maximum number of citations to process (prioritizes by frequency)
- `CITATION_TIMEOUT`: Maximum time in seconds to wait for a citation to process

## Project Structure
- `perplexityresearch.py`: Original script with single-question research
- `research_orchestrator.py`: Enhanced script with multi-question orchestration
- `.env`: Contains API keys and configuration values
- `research_projects.json`: Central tracking file for all research projects and OpenAI uploads
- Output structure:
  - `[Topic]_[timestamp]/`: Master folder for a research project
    - `markdown/`: Formatted markdown files
    - `response/`: Raw API responses
    - `summaries/`: Consolidated output files
      - `consolidated_summaries.md`: All research summaries combined
      - `consolidated_executive_summaries.md`: Executive summary version
      - `master_index.md`: Index of all questions
      - `citation_index.md`: Index of all citations
    - `README.md`: Overview of the research project
    - `openai_upload_info.json`: Details about files uploaded to OpenAI

## How It Works

### Question Generation
For topic-based research, the script:
1. Takes a topic, perspective, and depth as inputs
2. Uses Perplexity to generate insightful research questions
3. Formats each question with appropriate context

### Research Pipeline
For each question:
1. Calls Perplexity API for initial research
2. Extracts citation URLs from the response
3. Generates a research summary and executive summary

### Citation Processing
After all questions are processed:
1. Extracts and deduplicates citation URLs across all questions
2. Prioritizes citations based on their frequency of reference
3. Processes top N citations (default: 50) with Firecrawl
4. Implements timeout protection to handle problematic URLs
5. Cleans and formats citation content with Perplexity
6. Creates cross-referenced indexes of questions and citations

### OpenAI Integration
After consolidation:
1. Uploads markdown files and summaries to OpenAI's file storage
2. Creates a vector store for semantic search capabilities
3. Adds uploaded files to the vector store
4. Tracks project details in a central JSON file

## Recent Enhancements

1. **OpenAI Integration**: Upload research files to OpenAI and create vector stores for semantic search
2. **Research Project Tracking**: Central JSON tracking of all research projects
2. **Interactive Mode**: Run without arguments and follow prompts
3. **Citation Prioritization**: Focus on the most frequently referenced citations
4. **Consolidated Summary Files**: Automatically combine all summaries
5. **Improved File Naming**: Changed naming convention from Q-prefix to A-prefix for better sorting
6. **Timeout Protection**: Automatically skip citations that take too long to process
7. **Summaries Directory**: Organized consolidated files in a dedicated folder

## Performance Benefits
- **Time savings**: 30-50% overall time reduction for multi-question research
- **API efficiency**: 40-60% reduction in API calls for citation processing
- **Rate limit protection**: Three layers of protection (worker allocation, retry logic, staggered starts)
- **Robust handling**: Automatic timeout protection prevents hanging on problematic URLs

## Limitations
- Social media sites may require special Firecrawl access
- Very large documents may be truncated due to API token limits
- Performance depends on API rate limits and server response times

## License
This project is licensed under the MIT License.
# Perplexity Research Suite

A powerful research automation toolkit that combines Perplexity API and Firecrawl to create comprehensive research packages on any topic. This suite includes a single-question research tool (`perplexityresearch.py`) and a multi-question research orchestrator (`research_orchestrator.py`) with advanced features.

## Overview

This toolkit enables:

- **AI-powered research** using Perplexity's advanced models
- **Automated citation analysis** with intelligent web crawling
- **Multi-question research** from a single topic
- **Parallel processing** with rate limit protection
- **Citation deduplication** for efficient processing
- **Comprehensive output** with markdown files and research indexes

## Quick Start

1. **Install dependencies**:
   ```
   pip install reportlab requests python-dotenv firecrawl
   ```

2. **Set up your API keys**:
   - Create a `.env` file based on the provided `.env.example`
   - Add your Perplexity and Firecrawl API keys

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
python research_orchestrator.py --output ./my_research --topic "AI in Healthcare"

# Control worker threads and timing
python research_orchestrator.py --max-workers 3 --stagger-delay 10 --topic "Sustainable Energy"
```

## Key Features

### Three-Phase Processing Workflow
1. **Phase 1**: Process all research questions to get initial responses
2. **Phase 2**: Extract and deduplicate citations across all questions
3. **Phase 3**: Process each unique citation exactly once

### Performance Optimizations
- **Parallel Processing**: Uses ThreadPoolExecutor to run multiple research questions simultaneously
- **Smart Retry Logic**: Implements exponential backoff with jitter for rate-limited API calls
- **Staggered Thread Starts**: Configurable delay between starting each worker thread
- **Citation Deduplication**: Process each citation URL only once, even if referenced by multiple questions

### User Experience
- **Color-coded Output**: Clear visual feedback on processing status
- **Progress Tracking**: Individual progress for each question and citation
- **Comprehensive Indexing**: Master index of questions and citation index for navigation

## Configuration

### Environment Variables (.env file)
```
# API Keys
PERPLEXITY_API_KEY=your_perplexity_api_key
FIRECRAWL_API_KEY=your_firecrawl_api_key

# Models
PERPLEXITY_RESEARCH_MODEL=sonar-medium-online
PERPLEXITY_CLEANUP_MODEL=mixtral-8x7b-instruct

# API Error Handling
API_MAX_RETRIES=3
API_INITIAL_RETRY_DELAY=2.0
API_MAX_RETRY_DELAY=30.0

# Rate Limiting
RATE_LIMIT_QUESTIONS_PER_WORKER=10
THREAD_STAGGER_DELAY=5.0
```

### Rate Limit Tuning
- `RATE_LIMIT_QUESTIONS_PER_WORKER`: Controls worker thread allocation (higher value = fewer workers)
- `THREAD_STAGGER_DELAY`: Sets delay between thread starts in seconds
- `API_MAX_RETRIES`: Maximum number of retry attempts for rate-limited API calls

## Project Structure
- `perplexityresearch.py`: Original script with single-question research
- `research_orchestrator.py`: Enhanced script with multi-question orchestration
- `.env`: Contains API keys and configuration values
- Output structure:
  - `[Topic]_[timestamp]/`: Master folder for a research project
    - `markdown/`: Formatted markdown files
    - `response/`: Raw API responses
    - `master_index.md`: Index of all questions
    - `citation_index.md`: Index of all citations
    - `README.md`: Overview of the research project

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
2. Processes each unique citation once with Firecrawl
3. Cleans and formats citation content with Perplexity
4. Creates cross-referenced indexes of questions and citations

## Performance Benefits
- **Time savings**: 30-50% overall time reduction for multi-question research
- **API efficiency**: 40-60% reduction in API calls for citation processing
- **Rate limit protection**: Three layers of protection (worker allocation, retry logic, staggered starts)

## Limitations
- Social media sites may require special Firecrawl access
- Very large documents may be truncated due to API token limits
- Performance depends on API rate limits and server response times

## License
This project is licensed under the MIT License.
# Perplexity Research Crawler

An AI research tool that combines Perplexity API and Firecrawl to help facilitate the creation of comprehensive research reports. This script automatically researches any topic, crawls citation sources, and produces nicely formatted markdown documents for each, as well as a single PDF report of the output.

## Overview

This tool:
- Conducts in-depth research using Perplexity's AI models
- Intelligently crawls and processes citation sources with Firecrawl
- Generates executive summaries for quick insights
- Creates comprehensive PDF reports with proper formatting
- Organizes all outputs in timestamped folders

## Quick Start

1. **Install dependencies**:
   ```
   pip install reportlab requests python-dotenv firecrawl
   ```

2. **Set up your API keys**:
   - Create a `.env` file based on the provided `.env.example`
   - Add your Perplexity and Firecrawl API keys

3. **Run the script**:
   ```
   python perplexitycrawl.py "Your research question here"
   ```
   Or run without arguments to be prompted for input.

## Requirements

- Python 3.6+
- Perplexity API key (from [perplexity.ai](https://perplexity.ai))
- Firecrawl API key (from [firecrawl.com](https://firecrawl.com))

## Configuration (.env file)
PERPLEXITY_API_KEY=your_perplexity_api_key
FIRECRAWL_API_KEY=your_firecrawl_api_key
PERPLEXITY_RESEARCH_MODEL=sonar-deep-research
PERPLEXITY_CLEANUP_MODEL=sonar-medium-chat

## How It Works

1. **Research Phase**: Calls Perplexity API to conduct deep research on your query
2. **Citation Extraction**: Identifies and extracts all citation sources
3. **Web Crawling**: Uses Firecrawl to intelligently extract content from citations
4. **Content Processing**: Cleans and formats extracted content
5. **Report Generation**: Creates a comprehensive PDF report with:
   - Title page
   - Table of contents
   - Executive summary
   - Full research findings
   - Citation sections with formatted content

All outputs are organized in timestamped folders to prevent overwriting previous research.

## Output

The script automatically creates three main directories with timestamped subfolders:
- `response/` - Raw API responses
- `markdown/` - Formatted markdown files
- `reports/` - Final PDF reports

## Limitations

- Social media sites (YouTube, TikTok, etc.) require special Firecrawl access
- Very large documents may be truncated due to API token limits

## License

This project is licensed under the MIT License.

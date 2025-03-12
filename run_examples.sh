#!/bin/bash
# Run Examples for Perplexity Research Suite
# This script provides examples of different ways to run the research orchestrator

# Example 1: Interactive Mode (simplest)
echo "Example 1: Interactive Mode"
echo "python research_orchestrator.py"
echo ""

# Example 2: Topic Mode (generate questions automatically)
echo "Example 2: Topic Mode"
echo "python research_orchestrator.py --topic \"Artificial Intelligence in Healthcare\" --perspective \"Medical Director\" --depth 3"
echo ""

# Example 3: Direct Question Mode (specify your own questions)
echo "Example 3: Direct Question Mode with inline questions"
echo "python research_orchestrator.py --questions \"What is quantum computing?\" \"How do quantum computers work?\""
echo ""

# Example 4: Questions from file
echo "Example 4: Direct Question Mode with questions from file"
echo "python research_orchestrator.py --questions questions.txt"
echo ""

# Example 5: Custom output directory
echo "Example 5: Custom output directory"
echo "python research_orchestrator.py --output ./custom_research --topic \"Climate Change\""
echo ""

# Example 6: Control worker threads and timing
echo "Example 6: Custom worker threads and stagger delay"
echo "python research_orchestrator.py --max-workers 2 --stagger-delay 10 --topic \"Renewable Energy\""
echo ""

# Example 7: Limit citation processing
echo "Example 7: Custom maximum citations"
echo "python research_orchestrator.py --max-citations 20 --topic \"Machine Learning\""
echo ""

# Example 8: Combined options
echo "Example 8: Combined options"
echo "python research_orchestrator.py --topic \"Blockchain Technology\" --perspective \"CTO\" --depth 5 --max-workers 3 --max-citations 30 --output ./blockchain_research"
echo ""

# Create example questions file
echo "Creating example questions.txt file"
cat > questions.txt << EOL
What is the history of artificial intelligence?
How does machine learning differ from deep learning?
What are the ethical implications of AI?
How is AI being used in healthcare?
What is the future of AI in business?
EOL

echo ""
echo "You can run any of these examples by copying and pasting the command."
echo "The questions.txt file has been created for Example 4." 
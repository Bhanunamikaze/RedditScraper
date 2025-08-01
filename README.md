# Reddit Scraper

A comprehensive Reddit scraper that collects posts and comments from Reddit using multiple search strategies. Designed for research and data collection with built-in deduplication and continuous file writing.

## Features

- **5 Different Search Modes** for flexible data collection
- **Smart Default Behavior** - automatically chooses the most relevant search method
- **Real-time Data Writing** - continuously saves results to prevent data loss
- **Automatic Deduplication** - removes duplicate posts across different search methods
- **Comprehensive Comment Collection** - fetches full comment trees for each post
- **Rate Limiting Protection** - built-in retry logic and configurable delays
- **Flexible Time Windows** - supports both relative and absolute date ranges
- **NDJSON Output Format** - easy to process with data analysis tools

## Installation

### Prerequisites

```bash
# Install required Python packages
pip install aiohttp dateutil tenacity
```

### Dependencies

- **Python 3.8+**
- **aiohttp** - Async HTTP client
- **python-dateutil** - Date parsing
- **tenacity** - Retry logic for API calls

## Usage

### Basic Command Structure

```bash
python reddit_scraper.py [OPTIONS]
```

### Search Modes

#### 1. **Smart Mode (Default)**
Searches keywords within specific subreddits only.

```bash
python reddit_scraper.py --keywords keywords.txt --subreddits subreddits.txt --since 1m
```

#### 2. **Comprehensive Mode (Recommended)**
Combines global keyword search + targeted keyword-in-subreddit search.

```bash
python reddit_scraper.py --keywords keywords.txt --subreddits subreddits.txt --global-plus-targeted --since 1m
```

#### 3. **Traditional Mode**
Global keyword search + complete subreddit dumps.

```bash
python reddit_scraper.py --keywords keywords.txt --subreddits subreddits.txt --all-subreddits --since 1m
```

#### 4. **Keywords Only**
Global search across all Reddit.

```bash
python reddit_scraper.py --keywords keywords.txt --since 1m
```

#### 5. **Subreddits Only**
Dump all posts from specific subreddits.

```bash
python reddit_scraper.py --subreddits subreddits.txt --since 1m
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--keywords FILE` | File with one keyword per line | - |
| `--subreddits FILE` | File with one subreddit per line | - |
| `--global-plus-targeted` | Enable comprehensive search mode | False |
| `--all-subreddits` | Enable traditional mode | False |
| `--out FILE` | Output NDJSON file (appends) | `reddit.ndjson` |
| `--since DURATION` | Relative time window (30d, 6m, 2y) | `1y` |
| `--from DATE` | Absolute start date (YYYY-MM-DD) | - |
| `--to DATE` | Absolute end date (YYYY-MM-DD) | Today |
| `--sleep SECONDS` | Delay between API calls | `3` |
| `--post-limit N` | Max posts per search (0 = unlimited) | `0` |

## Input File Formats

### keywords.txt
```
machine learning
artificial intelligence
data science
python programming
cryptocurrency
```

### subreddits.txt
```
MachineLearning
artificial
datascience
Python
programming
technology
AskReddit
```

**Note:** The script automatically handles `r/` prefixes in subreddit names.

## Output Format

The scraper outputs data in **NDJSON** (Newline Delimited JSON) format, with each line containing a complete record:

### Post Record Example
```json
{
  "source": "reddit",
  "keyword": "targeted:machine learning@MachineLearning",
  "type": "post",
  "post_id": "abc123",
  "timestamp": "2025-08-01T14:30:00+00:00",
  "url": "https://www.reddit.com/r/MachineLearning/comments/abc123/title",
  "title": "Best practices for training neural networks",
  "content": "I've been working on a project and wondering about...",
  "meta": {
    "subreddit": "MachineLearning",
    "author": "username",
    "score": 15,
    "num_comments": 8
  }
}
```

### Comment Record Example
```json
{
  "source": "reddit",
  "keyword": "targeted:machine learning@MachineLearning",
  "type": "comment",
  "comment_id": "def456",
  "timestamp": "2025-08-01T15:00:00+00:00",
  "url": "https://www.reddit.com/comments/abc123/_/def456",
  "content": "You should consider using regularization techniques...",
  "post_context": {
    "post_id": "abc123",
    "title": "Best practices for training neural networks",
    "content": "I've been working on a project and wondering about..."
  },
  "meta": {
    "subreddit": "MachineLearning",
    "author": "commenter",
    "score": 3,
    "parent_comment_id": null
  }
}
```

## Search Term Labeling

The scraper automatically labels data based on the search method used:

- **`global:keyword`** - Found via global Reddit search
- **`subreddit:name`** - Found via subreddit dump
- **`targeted:keyword@subreddit`** - Found via keyword search within subreddit

## Examples

### Research Use Case: Technology Trends

```bash
# Comprehensive technology research
python reddit_scraper.py \
  --keywords tech_keywords.txt \
  --subreddits tech_subreddits.txt \
  --global-plus-targeted \
  --since 2y \
  --out tech_research.jsonl \
  --sleep 2
```

**tech_keywords.txt:**
```
artificial intelligence
machine learning
blockchain
cloud computing
cybersecurity
```

**tech_subreddits.txt:**
```
technology
MachineLearning
artificial
programming
datascience
```

### Market Research: Product Discussions

```bash
# Analyze product discussions and reviews
python reddit_scraper.py \
  --keywords product_terms.txt \
  --subreddits review_subreddits.txt \
  --global-plus-targeted \
  --since 6m \
  --out product_analysis.jsonl
```

### Academic Research: Social Media Sentiment

```bash
# Collect data for sentiment analysis
python reddit_scraper.py \
  --keywords social_topics.txt \
  --subreddits discussion_subreddits.txt \
  --since 1y \
  --post-limit 100 \
  --out sentiment_data.jsonl
```

### Quick Analysis: Recent Discussions

```bash
# Last 30 days of gaming discussions
python reddit_scraper.py \
  --keywords gaming_terms.txt \
  --subreddits gaming_subreddits.txt \
  --since 30d \
  --post-limit 50 \
  --out recent_gaming.jsonl
```

### Brand Monitoring: Global Mentions

```bash
# Find all mentions of specific topics across Reddit
python reddit_scraper.py \
  --keywords brand_names.txt \
  --since 6m \
  --out brand_mentions.jsonl
```

## Advanced Features

### **Automatic Deduplication**
Posts found through multiple search methods are automatically deduplicated using post IDs.

### **Continuous Writing**
Results are written to file immediately, preventing data loss if the script is interrupted.

### **Rate Limiting Protection**
Built-in exponential backoff retry logic handles Reddit's rate limits gracefully.

### **Comment Tree Collection**
Automatically fetches complete comment threads for each post, maintaining parent-child relationships.

## Data Analysis

### Load Data with Python
```python
import json
import pandas as pd

# Load NDJSON data
def load_reddit_data(filename):
    records = []
    with open(filename, 'r') as f:
        for line in f:
            records.append(json.loads(line))
    return pd.DataFrame(records)

df = load_reddit_data('reddit.ndjson')
```

### Filter by Search Type
```python
# Global mentions only
global_posts = df[df['keyword'].str.startswith('global:')]

# Targeted mentions only  
targeted_posts = df[df['keyword'].str.startswith('targeted:')]

# Posts vs Comments
posts = df[df['type'] == 'post']
comments = df[df['type'] == 'comment']
```

### Basic Analysis Examples
```python
# Most active subreddits
df['meta'].apply(pd.Series)['subreddit'].value_counts()

# Posts by time period
df['timestamp'] = pd.to_datetime(df['timestamp'])
daily_posts = df.groupby(df['timestamp'].dt.date).size()

# Top scoring posts
top_posts = df[df['type'] == 'post'].nlargest(10, 'meta.score')
```

## Use Cases

- **Academic Research** - Collect data for social media studies
- **Market Research** - Analyze product discussions and sentiment
- **Trend Analysis** - Track emerging topics and technologies
- **Brand Monitoring** - Monitor mentions across Reddit
- **Content Analysis** - Study community discussions and opinions
- **Data Science Projects** - Gather training data for ML models

## Limitations

- **API Rate Limits**: Reddit has strict rate limiting; the script includes delays and retry logic
- **Historical Data**: Reddit's search is limited for very old posts (>1 year can be inconsistent)
- **Comment Depth**: Limited to 500 comments per post due to Reddit's API constraints
- **Search Accuracy**: Reddit's search algorithm may not catch all relevant posts

## Troubleshooting

### Common Issues

**`TypeError: '_io.TextIOWrapper' object does not support the asynchronous context manager protocol`**
- This was fixed in v5.0 - ensure you're using the latest version

**Rate Limiting (429 errors)**
- Increase `--sleep` value (try 5-10 seconds)
- Reddit has stricter limits during peak hours

**No Results Found**
- Check that subreddit names are correct (without `r/` prefix)
- Verify keywords are not too specific
- Try a broader time window with `--since`

**Memory Issues**
- Use `--post-limit` to cap results per search
- The script writes continuously, so memory usage should be stable

## License

This project is licensed under the MIT License - see the LICENSE file for details.



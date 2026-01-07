#!/usr/bin/env python3
"""
Export a Claude Code conversation to markdown.

Reads the .jsonl conversation log and transforms it into a clean,
readable markdown file suitable for sharing.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')[:50]


def extract_user_content(message: dict) -> str | None:
    """Extract text content from a user message."""
    content = message.get('content')

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        # Handle tool results or multi-part messages
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'tool_result':
                    # Skip tool results - they're internal
                    continue
                if 'text' in item:
                    texts.append(item['text'])
            elif isinstance(item, str):
                texts.append(item)
        return '\n'.join(texts) if texts else None

    return None


def extract_assistant_content(message: dict) -> tuple[str | None, list[str]]:
    """
    Extract text content and tool names from an assistant message.

    Returns:
        tuple of (text_content, list_of_tool_names)
    """
    content = message.get('content', [])

    if not isinstance(content, list):
        return None, []

    texts = []
    tools = []

    for item in content:
        if not isinstance(item, dict):
            continue

        item_type = item.get('type')

        if item_type == 'text':
            text = item.get('text', '').strip()
            if text:
                texts.append(text)

        elif item_type == 'tool_use':
            tool_name = item.get('name', 'Unknown')
            tools.append(tool_name)

        # Skip 'thinking' blocks entirely

    text_content = '\n\n'.join(texts) if texts else None
    return text_content, tools


def parse_conversation(jsonl_path: Path) -> list[dict]:
    """
    Parse a .jsonl conversation file into structured turns.

    Returns:
        List of turn dicts with keys: role, content, tools, timestamp
    """
    turns = []
    seen_content = set()  # Deduplicate repeated assistant chunks

    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get('type')
            timestamp = entry.get('timestamp')

            if entry_type == 'user':
                message = entry.get('message', {})
                content = extract_user_content(message)

                if content and content not in seen_content:
                    seen_content.add(content)
                    turns.append({
                        'role': 'user',
                        'content': content,
                        'tools': [],
                        'timestamp': timestamp
                    })

            elif entry_type == 'assistant':
                message = entry.get('message', {})
                content, tools = extract_assistant_content(message)

                # Only add if there's actual text content (not just tool calls)
                if content and content not in seen_content:
                    seen_content.add(content)
                    turns.append({
                        'role': 'assistant',
                        'content': content,
                        'tools': tools,
                        'timestamp': timestamp
                    })
                elif tools and not content:
                    # Tool-only message - just note the tools used
                    # Check if last turn was assistant and merge tools
                    if turns and turns[-1]['role'] == 'assistant':
                        turns[-1]['tools'].extend(tools)

    return turns


def format_markdown(turns: list[dict], topic: str, session_id: str) -> str:
    """Format conversation turns as markdown."""
    lines = []

    # Header
    first_timestamp = turns[0]['timestamp'] if turns else None
    if first_timestamp:
        try:
            dt = datetime.fromisoformat(first_timestamp.replace('Z', '+00:00'))
            date_str = dt.strftime('%Y-%m-%d %H:%M')
        except (ValueError, AttributeError):
            date_str = 'Unknown'
    else:
        date_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    lines.append(f'# Conversation: {topic}')
    lines.append('')
    lines.append(f'**Date:** {date_str}')
    lines.append(f'**Session:** {session_id}')
    lines.append('')
    lines.append('---')
    lines.append('')

    # Turns
    for turn in turns:
        role = turn['role']
        content = turn['content']
        tools = turn.get('tools', [])

        if role == 'user':
            lines.append('## User')
        else:
            lines.append('## Claude')

        lines.append('')
        lines.append(content)
        lines.append('')

        # Add tool usage notes
        for tool in tools:
            lines.append(f'> Used tool: {tool}')

        if tools:
            lines.append('')

        lines.append('---')
        lines.append('')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Export a Claude Code conversation to markdown'
    )
    parser.add_argument(
        '--session-id',
        required=True,
        help='The session UUID'
    )
    parser.add_argument(
        '--project-path',
        required=True,
        help='The encoded project folder name (e.g., -Users-ph--claude-skills)'
    )
    parser.add_argument(
        '--topic',
        default=None,
        help='Topic slug for the filename (optional)'
    )

    args = parser.parse_args()

    # Construct path to .jsonl file
    claude_dir = Path.home() / '.claude'
    jsonl_path = claude_dir / 'projects' / args.project_path / f'{args.session_id}.jsonl'

    if not jsonl_path.exists():
        print(f'Error: Conversation file not found: {jsonl_path}', file=sys.stderr)
        sys.exit(1)

    # Parse conversation
    turns = parse_conversation(jsonl_path)

    if not turns:
        print('Error: No conversation content found', file=sys.stderr)
        sys.exit(1)

    # Determine topic
    topic = args.topic
    if not topic:
        # Try to extract slug from the conversation
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if 'slug' in entry:
                        topic = entry['slug']
                        break
                except json.JSONDecodeError:
                    continue

    if not topic:
        topic = 'conversation'

    topic_slug = slugify(topic)

    # Format output
    markdown = format_markdown(turns, topic, args.session_id)

    # Determine output path
    skill_dir = Path.home() / '.claude' / 'skills' / 'save-conversation'
    transcripts_dir = skill_dir / 'transcripts'
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    date_prefix = datetime.now().strftime('%Y-%m-%d')
    output_filename = f'{date_prefix}-{topic_slug}.md'
    output_path = transcripts_dir / output_filename

    # Handle filename collisions
    counter = 1
    while output_path.exists():
        output_filename = f'{date_prefix}-{topic_slug}-{counter}.md'
        output_path = transcripts_dir / output_filename
        counter += 1

    # Write output
    output_path.write_text(markdown, encoding='utf-8')

    print(f'Saved: {output_path}')
    print(f'Turns: {len(turns)}')


if __name__ == '__main__':
    main()

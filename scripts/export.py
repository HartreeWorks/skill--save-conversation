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


def is_system_noise(text: str) -> bool:
    """
    Check if a message is system/command noise that should be skipped.

    Filters out:
    - Local command caveats
    - Command invocation messages (/clear, etc.)
    - Empty command stdout
    - System reminders
    - Skill SKILL.md injections
    """
    if not text:
        return True

    # Patterns that indicate pure system noise (no real user content)
    noise_patterns = [
        r'^<local-command-caveat>.*</local-command-caveat>\s*$',
        r'^<command-name>.*</command-name>\s*<command-message>.*</command-message>\s*<command-args>.*</command-args>\s*$',
        r'^<local-command-stdout>.*</local-command-stdout>\s*$',
        r'^\s*$',  # Empty or whitespace only
    ]

    # Check for skill injection (SKILL.md content loaded by Skill tool)
    if text.strip().startswith('Base directory for this skill:'):
        return True

    # Check if the entire message matches a noise pattern
    for pattern in noise_patterns:
        if re.match(pattern, text, re.DOTALL | re.IGNORECASE):
            return True

    # Check if message is ONLY composed of system tags with no real content
    stripped = text
    system_tags = [
        r'<local-command-caveat>.*?</local-command-caveat>',
        r'<command-name>.*?</command-name>',
        r'<command-message>.*?</command-message>',
        r'<command-args>.*?</command-args>',
        r'<local-command-stdout>.*?</local-command-stdout>',
        r'<system-reminder>.*?</system-reminder>',
    ]
    for tag_pattern in system_tags:
        stripped = re.sub(tag_pattern, '', stripped, flags=re.DOTALL)

    # If nothing meaningful remains after stripping system tags, it's noise
    if not stripped.strip():
        return True

    return False


def clean_user_content(text: str) -> str:
    """Remove system tags from user content while preserving real content."""
    # Remove system reminder tags
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    # Remove local command caveat tags
    text = re.sub(r'<local-command-caveat>.*?</local-command-caveat>', '', text, flags=re.DOTALL)
    return text.strip()


def extract_user_answers(message: dict) -> list[str]:
    """
    Extract user answers from AskUserQuestion tool results.

    Returns:
        List of answer strings like '"Question"="Answer"'
    """
    content = message.get('content', [])
    answers = []

    if not isinstance(content, list):
        return answers

    for item in content:
        if isinstance(item, dict) and item.get('type') == 'tool_result':
            result_content = item.get('content', '')
            if isinstance(result_content, str):
                # Look for the answer pattern
                match = re.search(
                    r'User has answered your questions?:\s*(.+?)(?:\.\s*You can now continue|$)',
                    result_content,
                    re.DOTALL
                )
                if match:
                    answers.append(match.group(1).strip())

    return answers


def extract_user_content(message: dict) -> str | None:
    """Extract text content from a user message."""
    content = message.get('content')

    if isinstance(content, str):
        if is_system_noise(content):
            return None
        return clean_user_content(content)

    if isinstance(content, list):
        # Handle tool results or multi-part messages
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'tool_result':
                    # Skip tool results - they're internal (answers handled separately)
                    continue
                if 'text' in item:
                    text = item['text']
                    if not is_system_noise(text):
                        texts.append(clean_user_content(text))
            elif isinstance(item, str):
                if not is_system_noise(item):
                    texts.append(clean_user_content(item))

        combined = '\n'.join(texts) if texts else None
        if combined and is_system_noise(combined):
            return None
        return combined

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
        List of turn dicts with keys: role, content, tools, answers, timestamp
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

                # Check for AskUserQuestion answers
                answers = extract_user_answers(message)
                if answers and turns and turns[-1]['role'] == 'assistant':
                    # Attach answers to the previous assistant turn
                    if 'answers' not in turns[-1]:
                        turns[-1]['answers'] = []
                    turns[-1]['answers'].extend(answers)

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


def shift_headings(text: str) -> str:
    """
    Shift heading levels down by one to avoid conflicts with conversation structure.

    H2 → H3, H3 → H4, H4 → H5, H5 → H6, H6 stays H6
    """
    # Process line by line to handle headings at start of lines
    lines = text.split('\n')
    result = []

    for line in lines:
        # Match markdown headings (## to ######)
        match = re.match(r'^(#{2,6})\s', line)
        if match:
            hashes = match.group(1)
            if len(hashes) < 6:
                # Add one more hash (shift down)
                line = '#' + line
        result.append(line)

    return '\n'.join(result)


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
        answers = turn.get('answers', [])

        if role == 'user':
            lines.append('## User')
        else:
            lines.append('## Claude')
            # Shift heading levels to avoid conflicts with conversation structure
            content = shift_headings(content)

        lines.append('')
        lines.append(content)
        lines.append('')

        # Add tool usage notes
        for tool in tools:
            lines.append(f'> Used tool: {tool}')

        if tools:
            lines.append('')

        # Add user answers from AskUserQuestion
        if answers:
            for answer in answers:
                # Parse "Question"="Answer" format
                parts = re.findall(r'"([^"]+)"="([^"]+)"', answer)
                for question, response in parts:
                    lines.append(f'**{question}**')
                    lines.append('')
                    lines.append(response)
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

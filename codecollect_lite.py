# code_collect_lite.py

import os
import json
import sys

# --- Configuration ---

# Set to True to exclude folder structure and remove comment lines (#)
# Set to False for the full output including structure and comments.
CONCISE_OUTPUT = False # <-- ADDED FLAG

# Output directory (will be created if it doesn't exist)
# IMPORTANT: This directory MUST also be listed in IGNORED_FOLDERS
OUTPUT_DIR_NAME = "codebase"

# Files generated within the output directory
MODULES_FILENAME = "modules.json"
STRUCTURE_FILENAME = "folder_structure.txt"
CODEBASE_FILENAME = "codebase.txt"

# List of file extensions to include (lowercase, starting with '.')
ALLOWED_EXTENSIONS = [
    '.py',
    # '.yml', '.yaml',
    '.json',
    '.sh',
    '.html',
    '.js',
    '.css',
    # Add more extensions as needed
]

# List of specific filenames to *always* include if found (case-insensitive)
ALLOWED_FILENAMES = [
    'Dockerfile',
    'docker-compose.yml',
    'requirements.txt',
    # Add more specific filenames as needed
]

# List of directory names to completely ignore
IGNORED_FOLDERS = [
    '.git',
    '.venv',
    'env',
    'venv',
    '__pycache__',
    'node_modules',
    '.vscode',
    '.idea',
    'migrations',
    'seed_data',
    'captured_sessions',
    'output_recordings',
    OUTPUT_DIR_NAME, # Ignore the output directory itself!
    # Add more folders as needed
]

# List of specific filenames to *always* ignore (case-insensitive)
IGNORED_FILENAMES = [
    '.DS_Store',
    '__init__.py',
    '*.pyc', # Example pattern, simple check below handles extension
    os.path.basename(__file__), # Ignore the script itself
    # Add more specific filenames as needed
]

# List of file extensions to *always* ignore (lowercase, starting with '.')
IGNORED_EXTENSIONS = [
    '.pyc',
    '.log',
    '.bak',
    '.swp',
    # Add more extensions as needed
]


# Custom instructions/prompt to append at the end of the codebase file
# This is appended regardless of the CONCISE_OUTPUT flag
CUSTOM_PROMPT = """
---
**Coding Guidelines & Review Instructions:**

1.  **Consistency:** Ensure code style is consistent across all files.
2.  **Readability:** Code should be clear, well-commented where necessary, and easy to understand.
3.  **PEP 8 (Python):** Adhere to PEP 8 standards for Python code (e.g., max line length 79 chars, single/double quotes consistency, spacing).
4.  **Error Handling:** Check for robust error handling and edge cases.
5.  **Security:** Look for potential security vulnerabilities (e.g., hardcoded secrets, injection points).
6.  **Efficiency:** Identify any obvious performance bottlenecks.
7.  **Modularity:** Assess if the code is well-structured and modular.

**Please review the collected codebase above based on these points.**
---
"""

# --- Helper Functions ---

def get_script_dir():
    """Gets the directory where the script is located."""
    return os.path.dirname(os.path.abspath(__file__))

def get_output_dir(root_dir):
    """Gets the absolute path to the output directory."""
    return os.path.join(root_dir, OUTPUT_DIR_NAME)

def should_ignore(entry_path, root_dir):
    """
    Checks if a file or directory should be ignored based on configuration.
    """
    relative_path = os.path.relpath(entry_path, root_dir)
    path_parts = relative_path.split(os.sep)
    entry_name = os.path.basename(entry_path)
    _, entry_ext = os.path.splitext(entry_name)

    # Check ignored folders (applies to any part of the path)
    if any(part.lower() in [f.lower() for f in IGNORED_FOLDERS] for part in path_parts):
        return True

    # Check ignored filenames (case-insensitive)
    if entry_name.lower() in [f.lower() for f in IGNORED_FILENAMES]:
        return True

    # Check ignored extensions (case-insensitive)
    if entry_ext.lower() in IGNORED_EXTENSIONS:
        return True

    return False

def is_allowed_file(filename):
    """Checks if a filename is allowed based on configuration."""
    _, file_ext = os.path.splitext(filename)

    # Check allowed filenames (case-insensitive)
    if filename.lower() in [f.lower() for f in ALLOWED_FILENAMES]:
        return True

    # Check allowed extensions (case-insensitive)
    if file_ext.lower() in ALLOWED_EXTENSIONS:
        return True

    return False

def find_project_files(root_dir):
    """
    Scans the project directory and returns a sorted list of relative paths
    for allowed files, respecting ignore rules.
    """
    allowed_files = []
    output_dir_abs = get_output_dir(root_dir)

    for current_root, dirs, files in os.walk(root_dir, topdown=True):
        # Filter ignored directories
        dirs[:] = [d for d in dirs if not should_ignore(os.path.join(current_root, d), root_dir)]

        for file in files:
            file_path_abs = os.path.join(current_root, file)
            if not should_ignore(file_path_abs, root_dir) and is_allowed_file(file):
                relative_path = os.path.relpath(file_path_abs, root_dir).replace('\\', '/')
                allowed_files.append(relative_path)

    return sorted(allowed_files)

def generate_folder_structure(root_dir):
    """
    Generates a string representation of the folder structure, respecting
    ignore rules.
    """
    tree_lines = [os.path.basename(root_dir) + "/"]
    output_dir_abs = get_output_dir(root_dir)

    def build_tree(current_path, prefix=""):
        entries = []
        try:
            # Ensure we can list the directory; skip if permission denied etc.
            entries = sorted(os.listdir(current_path))
        except OSError as e:
            return [f"{prefix}└── [Error listing directory: {e.filename}]"]

        lines = []
        # Filter out ignored entries *before* determining connectors
        visible_entries = [
            e for e in entries
            if not should_ignore(os.path.join(current_path, e), root_dir)
        ]

        for i, entry in enumerate(visible_entries):
            entry_path_abs = os.path.join(current_path, entry)
            is_last = i == len(visible_entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry}")

            if os.path.isdir(entry_path_abs):
                extension = "    " if is_last else "│   "
                lines.extend(build_tree(entry_path_abs, prefix + extension))
        return lines

    tree_lines.extend(build_tree(root_dir))
    return "\n".join(tree_lines)

def save_json(filepath, data):
    """Saves data to a JSON file."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except IOError as e:
        print(f"Error: Could not write JSON file '{filepath}'. Reason: {e}", file=sys.stderr)
        return False
    except TypeError as e:
        print(f"Error: Data is not JSON serializable for '{filepath}'. Reason: {e}", file=sys.stderr)
        return False

def load_json(filepath):
    """Loads data from a JSON file."""
    if not os.path.exists(filepath):
        print(f"Error: JSON file not found: '{filepath}'", file=sys.stderr)
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except IOError as e:
        print(f"Error: Could not read JSON file '{filepath}'. Reason: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format in '{filepath}'. Reason: {e}", file=sys.stderr)
        return None

def save_text(filepath, content):
    """Saves text content to a file."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except IOError as e:
        print(f"Error: Could not write text file '{filepath}'. Reason: {e}", file=sys.stderr)
        return False

def read_file_content(filepath):
    """Reads text content from a file, handling potential encoding errors."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except IOError as e:
        print(f"Warning: Could not read file '{filepath}'. Reason: {e}. Skipping.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Warning: An unexpected error occurred reading '{filepath}'. Reason: {e}. Skipping.", file=sys.stderr)
        return None


def get_language_hint(filename):
    """Determines the language hint for Markdown code blocks based on filename."""
    _, ext = os.path.splitext(filename)
    ext = ext.lower()

    # Prioritize specific filenames
    name_lower = os.path.basename(filename).lower()
    if name_lower == 'dockerfile':
        return 'dockerfile'
    if name_lower == 'docker-compose.yml':
        return 'yaml'

    # Map extensions to hints
    hints = {
        '.py': 'python',
        '.js': 'javascript',
        '.html': 'html',
        '.css': 'css',
        '.json': 'json',
        '.yaml': 'yaml',
        '.yml': 'yaml',
        '.md': 'markdown',
        '.sh': 'bash',
        '.txt': 'text',
        '.sql': 'sql',
        '.xml': 'xml',
        # Add more mappings as needed
    }
    return hints.get(ext, '') # Return empty string if no hint known


# --- Main Execution Logic ---

def main():
    root_dir = get_script_dir()
    output_dir = get_output_dir(root_dir)
    modules_json_path = os.path.join(output_dir, MODULES_FILENAME)
    structure_txt_path = os.path.join(output_dir, STRUCTURE_FILENAME)
    codebase_txt_path = os.path.join(output_dir, CODEBASE_FILENAME)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    print(f"Using output directory: {output_dir}")
    if CONCISE_OUTPUT:
        print("Concise output mode enabled: Folder structure and comments (#) will be excluded.")

    # --- Generate Folder Structure (always needed for structure file) ---
    print("Generating folder structure...")
    folder_structure_string = generate_folder_structure(root_dir)
    if not save_text(structure_txt_path, folder_structure_string):
        print("Failed to save folder structure file. Aborting.", file=sys.stderr)
        return # Stop if we can't even save the structure file
    print(f"Folder structure saved to '{structure_txt_path}'")


    # --- Conditional Logic: Check for modules.json ---
    if not os.path.exists(modules_json_path):
        # --- First Run: Create modules.json, don't collect codebase ---
        print(f"'{MODULES_FILENAME}' not found. Scanning project for files...")
        project_files = find_project_files(root_dir)

        if not project_files:
             print("Warning: No allowed files found based on current configuration.", file=sys.stderr)

        modules_data = {"files_to_include": project_files}
        if save_json(modules_json_path, modules_data):
            print(f"Found {len(project_files)} files.")
            print(f"List of files to include saved to '{modules_json_path}'.")
            print("\nPlease review/edit this file if needed.")
            print("Run the script again to generate the combined codebase file.")
        else:
            print("Failed to save modules file. Aborting.", file=sys.stderr)

    else:
        # --- Subsequent Runs: Load modules.json, collect codebase ---
        print(f"Found '{modules_json_path}'. Loading file list...")
        modules_data = load_json(modules_json_path)

        if modules_data is None or "files_to_include" not in modules_data:
            print(f"Error: Could not load file list from '{modules_json_path}'. "
                  f"Delete the file and run again to regenerate it.", file=sys.stderr)
            return

        files_to_include = modules_data["files_to_include"]
        print(f"Collecting content for {len(files_to_include)} files listed in '{MODULES_FILENAME}'...")

        codebase_parts = []

        # --- MODIFIED: Conditionally Add Folder Structure ---
        if not CONCISE_OUTPUT:
            codebase_parts.append("## Folder Structure:\n")
            codebase_parts.append("```\n")
            # Read the structure we saved earlier (or use fallback)
            try:
                 with open(structure_txt_path, 'r', encoding='utf-8') as sf:
                     codebase_parts.append(sf.read())
            except IOError:
                 print(f"Warning: Could not re-read structure file '{structure_txt_path}'. Using generated string.", file=sys.stderr)
                 codebase_parts.append(folder_structure_string) # Fallback
            codebase_parts.append("```")
            codebase_parts.append("\n---\n") # Separator

        # Add File Contents
        collected_count = 0
        skipped_count = 0
        for relative_path in files_to_include:
            full_path = os.path.join(root_dir, relative_path)
            # Don't print processing message if concise to keep output cleaner
            if not CONCISE_OUTPUT:
                print(f"  - Processing: {relative_path}")

            if not os.path.exists(full_path):
                print(f"  - Warning: File not found: '{relative_path}'. Skipping.", file=sys.stderr)
                skipped_count += 1
                continue

            content = read_file_content(full_path)
            if content is None:
                skipped_count += 1
                continue # Error message already printed by read_file_content

            # --- MODIFIED: Conditionally process content for conciseness ---
            processed_content = content
            if CONCISE_OUTPUT:
                lines = content.splitlines()
                # Keep lines that *don't* start with # after stripping leading/trailing whitespace
                filtered_lines = [line for line in lines if not line.strip().startswith('#')]
                processed_content = '\n'.join(filtered_lines)
            # --- End concise processing ---

            lang_hint = get_language_hint(relative_path)
            codebase_parts.append(f"{relative_path}:\n")
            codebase_parts.append(f"```{lang_hint}\n")
            # Ensure content ends with a newline before the closing backticks
            # Use the (potentially modified) processed_content here
            codebase_parts.append(processed_content.rstrip() + '\n')
            codebase_parts.append("```\n")
            collected_count += 1

        # Add Custom Prompt/Instructions (always added)
        codebase_parts.append(CUSTOM_PROMPT)

        # Combine and Save Codebase File
        # Use join without newline; newlines are handled within the appended parts
        final_codebase_content = "".join(codebase_parts)
        if save_text(codebase_txt_path, final_codebase_content):
            print("-" * 30)
            print(f"Successfully collected content from {collected_count} files.")
            if skipped_count > 0:
                print(f"Skipped {skipped_count} files (not found or read errors).")
            print(f"Combined codebase saved to '{codebase_txt_path}'")
        else:
            print("Failed to save the final codebase file.", file=sys.stderr)


if __name__ == "__main__":
    main()
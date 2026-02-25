from tensor_grep.formatters.base import OutputFormatter
from tensor_grep.core.result import SearchResult
from tensor_grep.core.config import SearchConfig
from collections import defaultdict

class RipgrepFormatter(OutputFormatter):
    def __init__(self, config: SearchConfig = None):
        self.config = config or SearchConfig()
        
    def format(self, result: SearchResult) -> str:
        lines = []
        
        if self.config.count:
            if result.total_matches > 0 or self.config.include_zero:
                # Group counts by file to match ripgrep output
                counts_by_file = defaultdict(int)
                for match in result.matches:
                    counts_by_file[match.file] += 1
                    
                if not counts_by_file and result.total_matches > 0:
                    # Fallback if result matches aren't populated but total is
                    lines.append(f"{result.total_matches}")
                    return "\n".join(lines)
                    
                for file_path, count in counts_by_file.items():
                    if self.config.with_filename or (self.config.file_patterns is None and not self.config.no_filename and result.total_files > 1):
                        lines.append(f"{file_path}:{count}")
                    else:
                        lines.append(f"{count}")
            return "\n".join(lines)
            
        for match in result.matches:
            # Basic ripgrep-like output: file:line:text
            lines.append(f"{match.file}:{match.line_number}:{match.text}")
        return "\n".join(lines)

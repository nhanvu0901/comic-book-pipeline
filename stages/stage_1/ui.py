"""
Terminal UI helpers — colors, styled print functions, and user input.
"""
import sys


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.HEADER}══ {text} ══{Colors.END}\n")


def print_phase(phase_name, emoji="🔹"):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{emoji} PHASE: {phase_name}{Colors.END}")


def print_agent(text):
    """Print agent's conversational message."""
    print(f"{Colors.GREEN}🤖 PanelNarrator:{Colors.END} {text}")


def print_info(label, value):
    print(f"  {Colors.BOLD}{label}:{Colors.END} {value}")


def print_warning(text):
    print(f"  {Colors.YELLOW}⚠️  {text}{Colors.END}")


def print_error(text):
    print(f"  {Colors.RED}❌ {text}{Colors.END}")


def print_success(text):
    print(f"  {Colors.GREEN}✅ {text}{Colors.END}")


def print_list_item(idx, text, selected=False):
    marker = f"{Colors.GREEN}▶{Colors.END}" if selected else f"{Colors.DIM}│{Colors.END}"
    print(f"  {marker} {Colors.BOLD}[{idx}]{Colors.END} {text}")


def get_user_input(prompt_text="Your answer"):
    """Get input from user with styled prompt."""
    print()
    try:
        return input(f"  {Colors.BOLD}{Colors.BLUE}💬 {prompt_text}: {Colors.END}").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n\n  Cancelled.")
        sys.exit(0)

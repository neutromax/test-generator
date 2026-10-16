"""Shows the source lines of a component that no passing test has executed."""
import re


def parse_ranges(text):
    """Line numbers from a coverage range string such as '366-367, 369-381'."""
    numbers = set()
    for start, end in re.findall(r"(\d+)(?:-(\d+))?", str(text or "")):
        numbers.update(range(int(start), int(end or start) + 1))
    return sorted(numbers)


def annotated_source(lines, uncovered, context=2, limit=150):
    """Numbered source windows around uncovered lines; '!' marks the lines no passing test runs."""
    missing = {number for number in uncovered if 0 < number <= len(lines)}
    keep = sorted({n for number in missing
                   for n in range(max(1, number - context), min(len(lines), number + context) + 1)})
    out, previous = [], None
    for number in keep[:limit]:
        if previous is not None and number != previous + 1:
            out.append("      ...")
        out.append(f"{'!' if number in missing else ' '} {number:>5}  {lines[number - 1].rstrip()}")
        previous = number
    if len(keep) > limit:
        out.append(f"      ... {len(keep) - limit} more line(s)")
    return "\n".join(out)

"""Regenerate the colour section of app/src/main/assets/shell-theme.css from the official KiraAI panel.

The panel ships compiled Tailwind v3, so its blues and purples are literal values in each
utility rule, not variables. This tool reads every stylesheet in the panel's dist.zip, finds
each rule that uses a Tailwind blue, sky, indigo, violet or purple value (any format, any
alpha, inside or outside @media), and writes a rule with the same selector that uses one flat
colour of the same depth from the shell palette: blues become rose, purples become lilac.
No gradients are produced; the injected stylesheet only recolours, it never edits the panel.

usage: panel_palette.py [official-kira-seed.zip]    (default: build/runtime/assets/official-kira-seed.zip)
Run it again after the bundled official panel changes.
"""
import io
from pathlib import Path
import re
import sys
import zipfile

HERE = Path(__file__).resolve().parent
THEME = HERE / 'app/src/main/assets/shell-theme.css'
BEGIN, END = '/* panel-palette:begin', '/* panel-palette:end */'
SHADES = ('50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950')

# Tailwind v3 default palette, as the panel's compiled CSS uses it.
TAILWIND = {
    'blue': ['239 246 255', '219 234 254', '191 219 254', '147 197 253', '96 165 250', '59 130 246',
             '37 99 235', '29 78 216', '30 64 175', '30 58 138', '23 37 84'],
    'sky': ['240 249 255', '224 242 254', '186 230 253', '125 211 252', '56 189 248', '14 165 233',
            '2 132 199', '3 105 161', '7 89 133', '12 74 110', '8 47 73'],
    'indigo': ['238 242 255', '224 231 255', '199 210 254', '165 180 252', '129 140 248', '99 102 241',
               '79 70 229', '67 56 202', '55 48 163', '49 46 129', '30 27 75'],
    'violet': ['245 243 255', '237 233 254', '221 214 254', '196 181 253', '167 139 250', '139 92 246',
               '124 58 237', '109 40 217', '91 33 182', '76 29 149', '46 16 101'],
    'purple': ['250 245 255', '243 232 255', '233 213 255', '216 180 254', '192 132 252', '168 85 247',
               '147 51 234', '126 34 206', '107 33 168', '88 28 135', '59 7 100'],
}
# Shell palette: rose matches the app's primary #b84484 at 600, lilac its secondary tiles.
ROSE = ['fbf1f6', 'f6e2ed', 'edc6da', 'e0a2c2', 'd07aa8', 'c25a93', 'b84484', '9c386f', '7f2f5c', '64284b', '3d1a2e']
LILAC = ['f8f3fc', 'efe6f7', 'e0cfee', 'cdb6e6', 'b294d6', '9a78c4', '8463b0', '6f5297', '5a437a', '45335e', '2b1f3b']
TARGET = {'blue': ROSE, 'sky': ROSE, 'indigo': LILAC, 'violet': LILAC, 'purple': LILAC}

MAPPING, ROSE_ONLY = {}, {}
for family, values in TAILWIND.items():
    for index, value in enumerate(values):
        key = tuple(int(part) for part in value.split())
        MAPPING[key] = TARGET[family][index]
        # Glows on the login and setup pages stay in one hue: a rose glow next to a lilac one reads as pink-purple again.
        ROSE_ONLY[key] = ROSE[index]

_RGB = re.compile(r'rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})\s*(?:[,/]\s*([^)]+?))?\s*\)')
_HEX = re.compile(r'#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b')


def _swap(value):
    """Replace mapped colours inside one declaration value; returns (new value, replaced count)."""
    count = 0
    table = ROSE_ONLY if 'gradient' in value else MAPPING

    def rgb(match):
        nonlocal count
        target = table.get(tuple(int(match.group(i)) for i in (1, 2, 3)))
        if target is None:
            return match.group(0)
        count += 1
        red, green, blue = (int(target[i:i + 2], 16) for i in (0, 2, 4))
        alpha = match.group(4)
        return 'rgb(%d %d %d / %s)' % (red, green, blue, alpha.strip()) if alpha else '#' + target

    def hexadecimal(match):
        nonlocal count
        digits = match.group(1)
        if len(digits) == 3:
            digits = ''.join(ch * 2 for ch in digits)
        target = table.get(tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4)))
        if target is None:
            return match.group(0)
        count += 1
        return '#' + target + digits[6:]

    value = _RGB.sub(rgb, value)
    value = _HEX.sub(hexadecimal, value)
    return value, count


def _rules(css):
    """Yield (media prelude or None, selector, declarations) for every style rule, one @media level deep."""
    position, length = 0, len(css)
    stack = []
    start = 0
    while position < length:
        char = css[position]
        if char == '{':
            prelude = css[start:position].strip()
            stack.append(prelude)
            start = position + 1
        elif char == '}':
            if stack:
                prelude = stack.pop()
                body = css[start:position]
                if not prelude.startswith('@') and '{' not in body:
                    media = next((item for item in reversed(stack) if item.startswith('@media')), None)
                    if not any(item.startswith('@') and not item.startswith('@media') for item in stack):
                        yield media, prelude, body
            start = position + 1
        position += 1


def generate(seed):
    with zipfile.ZipFile(seed) as source, zipfile.ZipFile(io.BytesIO(source.read('dist.zip'))) as dist:
        sheets = sorted(name for name in dist.namelist()
                        if name.endswith('.css') and '/editor-' not in name and '/MonacoEditor' not in name)
        output, total = [], 0
        for name in sheets:
            css = dist.read(name).decode('utf-8')
            for media, selector, body in _rules(css):
                if selector.startswith(':root') or selector in ('html', 'body'):
                    continue
                declarations = []
                for declaration in body.split(';'):
                    if ':' not in declaration:
                        continue
                    prop, value = declaration.split(':', 1)
                    swapped, count = _swap(value.replace('!important', '').strip())
                    if count:
                        declarations.append('%s:%s !important' % (prop.strip(), swapped))
                        total += count
                if declarations:
                    rule = '%s{%s}' % (selector, ';'.join(declarations))
                    output.append(media + '{' + rule + '}' if media else rule)
    return output, total, len(sheets)


def main():
    seed = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / 'build/runtime/assets/official-kira-seed.zip'
    rules, total, sheets = generate(seed)
    block = '\n'.join([BEGIN + ': generated by panel_palette.py from the bundled official panel; edit the script, not this block. */']
                      + list(dict.fromkeys(rules)) + [END])
    theme = THEME.read_text(encoding='utf-8')
    if BEGIN in theme:
        before, rest = theme.split(BEGIN, 1)
        theme = before + block + rest.split(END, 1)[1]
    else:
        theme = theme.rstrip('\n') + '\n' + block + '\n'
    THEME.write_text(theme, encoding='utf-8')
    print('%d rules, %d colours replaced, from %d stylesheets' % (len(rules), total, sheets))


if __name__ == '__main__':
    main()

import re
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
THEME_RULE = re.compile(r'([^{}]+)\{([^{}]*)\}')
PAINT_PROPERTIES = {
    'accent-color', 'background', 'background-color', 'background-image',
    'border-color', 'box-shadow', 'caret-color', 'color', 'color-scheme',
    'filter', 'fill', 'outline-color', 'scrollbar-color', 'stroke', 'text-decoration-color',
    'text-shadow', '-webkit-text-fill-color',
}


class ThemeStyleTests(unittest.TestCase):
    def test_themes_only_override_paint_properties(self):
        css = (BASE / 'frontend' / 'style.css').read_text()
        violations = []
        for selectors, body in THEME_RULE.findall(css):
            if 'data-theme=' not in selectors:
                continue
            for declaration in body.split(';'):
                if ':' not in declaration:
                    continue
                name = declaration.split(':', 1)[0].strip()
                if not name.startswith('--') and name not in PAINT_PROPERTIES:
                    violations.append(f'{selectors.strip()}: {name}')
        self.assertEqual(violations, [], '主题只能覆盖配色，不能改变布局或字体')


if __name__ == '__main__':
    unittest.main()

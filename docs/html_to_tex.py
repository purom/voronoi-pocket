"""Builds docs/stress_method.tex from docs/voronoi_method.html.

    py docs\\html_to_tex.py

The HTML page is the one source of the text. This turns it into LaTeX: prose
is converted tag by tag, the numbered equations are typeset from the list
below (the page writes them with plain HTML), and figures point at the PDF
copies made by svg_to_pdf.py. Standard library only.
"""

import os
import re
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))

EQUATIONS = {
    1: r'A = \frac12 \sum_i \bigl(x_i\,y_{i+1} - x_{i+1}\,y_i\bigr)',
    2: r'c_x = \frac{1}{6A} \sum_i (x_i + x_{i+1})\bigl(x_i\,y_{i+1} - x_{i+1}\,y_i\bigr),'
       r'\qquad \text{and likewise for } c_y',
    3: r'x = (\mathbf{p} - \mathbf{o})\cdot\mathbf{u}, \qquad y = (\mathbf{p} - \mathbf{o})\cdot\mathbf{v}',
    4: r'\mathbf{m} = \mathbf{v} + d\,\frac{\mathbf{n}_1 + \mathbf{n}_2}{1 + \mathbf{n}_1\cdot\mathbf{n}_2}',
    5: r'\theta_{\max} = 2\arccos\!\left(1 - \frac{\varepsilon}{d}\right), \qquad \text{capped at } 15^\circ',
    6: r'(\mathbf{q} - \mathbf{p})\cdot\mathbf{x} \;\le\; \frac{|\mathbf{q}|^2 - |\mathbf{p}|^2}{2}',
    7: r'\delta > 2R',
    8: r'\text{core} = \operatorname{shrink}\bigl(\text{cell},\, \tfrac{w}{2} + r\bigr) \;\cap\; '
       r'\operatorname{shrink}\bigl(\text{face},\, m + r\bigr)',
    9: r'\text{pocket} = \operatorname{grow}(\text{core},\, r)',
    10: r'A_{\mathrm{sliver}} = \frac12\, r^2\,(\varphi - \sin\varphi)',
    11: r'A(w) \approx A_0 \left(1 - \frac{w - w_0}{S}\right)^{2}',
    12: r'w = w_0 + S\left(1 - \sqrt{A_{\mathrm{w}} / A_0}\right)',
    13: r'N_{\mathrm{new}} = N \left(\frac{w}{m}\right)^{2}',
    14: r'\sigma_x = d_{11}\,\varepsilon_x + d_{12}\,\varepsilon_y, \qquad '
        r'\sigma_y = d_{12}\,\varepsilon_x + d_{11}\,\varepsilon_y, \qquad \tau_{xy} = d_{33}\,\gamma_{xy}',
    15: r'd_{11} = \frac{E}{1 - \nu^2}, \qquad d_{12} = \nu\, d_{11}, \qquad d_{33} = d_{11}\,\frac{1 - \nu}{2}',
    16: r'b_i = y_j - y_k, \qquad c_i = x_k - x_j, \qquad 2A = x_1 b_1 + x_2 b_2 + x_3 b_3',
    17: r'\varepsilon_x = \frac{1}{2A}\sum_i b_i u_i, \qquad \varepsilon_y = \frac{1}{2A}\sum_i c_i v_i, \qquad '
        r'\gamma_{xy} = \frac{1}{2A}\sum_i \bigl(c_i u_i + b_i v_i\bigr)',
    18: r'\mathbf{K}_{pq} = \frac{t}{4A} \begin{pmatrix} '
        r'd_{11}\,b_p b_q + d_{33}\,c_p c_q & d_{12}\,b_p c_q + d_{33}\,c_p b_q \\[3pt] '
        r'd_{12}\,c_p b_q + d_{33}\,b_p c_q & d_{11}\,c_p c_q + d_{33}\,b_p b_q \end{pmatrix}',
    19: r'w_k = 1 - \frac{\lVert \mathbf{x}_k - \mathbf{x}_0 \rVert}{1.5\,h}, \qquad '
        r'\mathbf{f}_k = \mathbf{F}\,\frac{w_k}{\sum_j w_j}',
    20: r'\begin{aligned} \alpha &= \frac{\mathbf{r}\cdot\mathbf{z}}{\mathbf{p}\cdot\mathbf{K}\mathbf{p}}, & '
        r'\mathbf{u} &\leftarrow \mathbf{u} + \alpha\,\mathbf{p}, & '
        r'\mathbf{r} &\leftarrow \mathbf{r} - \alpha\,\mathbf{K}\mathbf{p}, \\ '
        r'\mathbf{z} &\leftarrow \frac{\mathbf{r}}{\operatorname{diag}(\mathbf{K})}, & '
        r'\mathbf{p} &\leftarrow \mathbf{z} + \frac{(\mathbf{r}\cdot\mathbf{z})_{\mathrm{new}}}'
        r'{(\mathbf{r}\cdot\mathbf{z})_{\mathrm{old}}}\,\mathbf{p} \end{aligned}',
    21: r'\sigma_{\mathrm{vM}} = \sqrt{\sigma_x^2 - \sigma_x\sigma_y + \sigma_y^2 + 3\,\tau_{xy}^2}',
    22: r'n = \frac{\sigma_{\mathrm{y}}}{\sigma_{\mathrm{vM}}}, \qquad '
        r'\sigma_{\mathrm{a}} = \frac{\sigma_{\mathrm{y}}}{n_{\mathrm{t}}}',
    23: r'\sigma_x = \frac{F\,(L - x)\,y}{I}, \qquad I = \frac{t\,H^3}{12}',
    24: r'\frac{\sigma}{\sigma_\infty} = 1 + \frac{a^2}{2r^2} + \frac{3a^4}{2r^4}',
    25: r'\kappa = \min\!\left(1,\; \frac{\sigma}{\sigma_{\mathrm{a}}}\right)',
    26: r'w = s\left(1 - \sqrt{1 - \kappa}\right)',
    27: r'\beta = \operatorname{clamp}\!\left(\frac{\sigma - \sigma_5}{\sigma_{95} - \sigma_5},\; 0,\; 1\right)',
    28: r'\omega = 1 - \left(1 - \frac1R\right)\beta, \qquad \rho = \frac{1}{\omega^2}',
    29: r'\mathbf{x}_{\mathrm{new}} = \frac{\int \mathbf{x}\,\phi(\mathbf{x})\,\mathrm{d}A}'
        r'{\int \phi(\mathbf{x})\,\mathrm{d}A}, \qquad \phi = \rho^2',
}

GREEK = {'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta', 'ε': 'varepsilon', 'η': 'eta',
         'θ': 'theta', 'κ': 'kappa', 'ν': 'nu', 'ξ': 'xi', 'ρ': 'rho', 'σ': 'sigma', 'τ': 'tau',
         'φ': 'varphi', 'ω': 'omega', 'Σ': 'Sigma'}
SYMBOLS = {'°': r'^\circ ', '²': '^2', '³': '^3', '⁴': '^4', '·': r'\cdot ', '½': r'\tfrac12 ',
           '×': r'\times ', '←': r'\leftarrow ', '−': '-', '√': r'\surd ', '∞': r'\infty ',
           '∩': r'\cap ', '∫': r'\int ', '≈': r'\approx ', '≤': r'\le ', '≥': r'\ge '}
SYMBOLS.update((ch, '\\%s ' % name) for ch, name in GREEK.items())
ESCAPES = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '#': r'\#', '_': r'\_', '{': r'\{',
           '}': r'\}', '$': r'\$', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}', '…': r'\ldots{}'}

PREAMBLE = r'''\documentclass[11pt,a4paper]{article}

\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[margin=24mm]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{float}
\usepackage{microtype}
\usepackage[hidelinks]{hyperref}

\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt plus 2pt}
\renewcommand{\arraystretch}{1.25}

\title{%s}
\author{Voronoi Pocket add-in for Autodesk Fusion, version 0.5.0}
\date{November 2026}

\begin{document}
\maketitle

\begin{abstract}
\noindent %s
\end{abstract}

\tableofcontents
\newpage
'''

ENDING = r'''
\section*{Further reading}

\begin{itemize}
  \item O.~C. Zienkiewicz, R.~L. Taylor, J.~Z. Zhu, \emph{The Finite Element Method: Its Basis and
        Fundamentals}, Butterworth-Heinemann. (The triangle element of section~14.)
  \item S.~P. Timoshenko, J.~N. Goodier, \emph{Theory of Elasticity}, McGraw-Hill. (Plate with a hole.)
  \item W.~D. Pilkey, D.~F. Pilkey, \emph{Peterson's Stress Concentration Factors}, Wiley.
        (A hole in a plate of finite width.)
  \item M.~R. Hestenes, E.~Stiefel, Methods of conjugate gradients for solving linear systems,
        \emph{J. Res. Natl. Bur. Stand.} 49 (1952).
  \item Q.~Du, V.~Faber, M.~Gunzburger, Centroidal Voronoi tessellations: applications and algorithms,
        \emph{SIAM Review} 41 (1999). (Evening out seed points, with and without weights.)
\end{itemize}

\end{document}
'''


class Converter(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.out = []          # the buffer being written
        self.saved = []        # buffers set aside while a part is captured
        self.skip = 0          # inside something left out
        self.math = []         # 'var' or 'sub' for every open maths tag
        self.title = self.abstract = ''
        self.lists = []
        self.table = None
        self.figure = None
        self.classes = []      # class of every open <p> / <div>
        self.equation = 0

    # --- small helpers
    def capture(self):
        self.saved.append(self.out)
        self.out = []

    def release(self):
        text = ''.join(self.out)
        self.out = self.saved.pop()
        return tidy(text)

    def emit(self, text):
        self.out.append(text)

    def open_math(self, kind):
        if not self.math:
            self.emit('$')
        self.math.append(kind)

    def close_math(self):
        self.math.pop()
        if not self.math:
            self.emit('$')

    # --- tags
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get('class', '')
        if self.skip:
            if tag not in ('meta', 'img', 'br', 'link'):  # these have no closing tag
                self.skip += 1
            return
        if tag in ('head', 'style', 'nav') or (tag == 'div' and cls == 'eq') or (tag == 'p' and cls == 'meta'):
            if tag == 'div':
                self.equation += 1
                self.emit('\n\\begin{equation}\n%s\n\\end{equation}\n' % EQUATIONS[self.equation])
            self.skip = 1
        elif tag in ('h1', 'h2', 'h3', 'figcaption', 'th', 'td', 'b') or (tag == 'p' and cls in ('lead', 'note')):
            if tag in ('th', 'td'):
                self.table['right'].append('r' in cls.split())
            self.classes.append(cls if tag == 'p' else None)
            self.capture()
        elif tag == 'p':
            self.classes.append(cls)
            self.emit('\n\n')
        elif tag in ('ul', 'ol'):
            env = 'enumerate' if tag == 'ol' else 'itemize'
            self.lists.append(env)
            self.emit('\n\\begin{%s}\n' % env)
        elif tag == 'li':
            self.emit('\n\\item ')
        elif tag == 'table':
            self.table = {'rows': [], 'right': []}
        elif tag == 'tr':
            self.table['row'] = []
            self.table['right'] = []
        elif tag == 'figure':
            self.figure = {'images': [], 'caption': '', 'row': 'row' in cls, 'narrow': False}
        elif tag == 'img':
            self.figure['images'].append(attrs['data-fig'][:-4])
            self.figure['narrow'] = 'max-width' in attrs.get('style', '')
        elif tag in ('var', 'i'):
            self.open_math('var')
        elif tag in ('sub', 'sup'):
            self.open_math('sub')
            self.emit('_{' if tag == 'sub' else '^{')
        elif tag == 'code':
            self.emit('\\texttt{')
        elif tag == 'em':
            self.emit('\\emph{')

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= 1
            return
        if tag == 'h1':
            self.title = self.release()
            self.classes.pop()
        elif tag in ('h2', 'h3'):
            text = re.sub(r'^\d+\.\s*', '', self.release())
            self.classes.pop()
            self.emit('\n\n\\%s{%s}\n' % ('section' if tag == 'h2' else 'subsection', text))
        elif tag == 'p':
            cls = self.classes.pop()
            if cls == 'lead':
                self.abstract = self.release()
            elif cls == 'note':
                self.emit('\n\n\\begin{quote}\n%s\n\\end{quote}\n' % self.release())
        elif tag == 'b':
            text = self.release()
            self.classes.pop()
            if len(text) <= 2 and text.isalnum():   # a vector such as p or u
                self.emit('\\mathbf{%s}' % text if self.math else '$\\mathbf{%s}$' % text)
            else:
                self.emit('\\textbf{%s}' % text)
        elif tag in ('ul', 'ol'):
            self.emit('\n\\end{%s}\n' % self.lists.pop())
        elif tag in ('th', 'td'):
            self.classes.pop()
            self.table['row'].append((self.release(), tag == 'th'))
        elif tag == 'tr':
            self.table['rows'].append((self.table['row'], list(self.table['right'])))
        elif tag == 'table':
            self.emit(table_tex(self.table['rows']))
            self.table = None
        elif tag == 'figcaption':
            self.classes.pop()
            self.figure['caption'] = re.sub(r'^Figure \d+\.\s*', '', self.release())
        elif tag == 'figure':
            self.emit(figure_tex(self.figure))
            self.figure = None
        elif tag in ('var', 'i'):
            self.close_math()
        elif tag in ('sub', 'sup'):
            self.emit('}')
            self.close_math()
        elif tag in ('code', 'em'):
            self.emit('}')

    def handle_data(self, data):
        if self.skip:
            return
        data = re.sub(r'\s+', ' ', data)
        if self.math:
            roman = self.math[-1] == 'sub' and 'var' not in self.math
            text = ''.join(SYMBOLS.get(ch, ESCAPES.get(ch, ch)) for ch in data)
            word = data.strip()
            if roman and len(word) > 0 and word.isalpha() and word.isascii():
                text = '\\mathrm{%s}' % word
            self.emit(text)
        else:
            self.emit(''.join('$%s$' % SYMBOLS[ch] if ch in SYMBOLS else ESCAPES.get(ch, ch) for ch in data))


def tidy(text):
    """Join maths that was opened and closed piece by piece: $x$$_{1}$ -> $x_{1}$."""
    text = text.replace('$$', '')
    return re.sub(r' +', ' ', text).strip()


def table_tex(rows):
    columns = len(rows[0][0])
    numeric = all(right for _, flags in rows for right in flags)
    if numeric:
        spec = 'r' * columns
    elif columns == 2:
        spec = r'>{\raggedright\arraybackslash}p{0.27\textwidth}>{\raggedright\arraybackslash}p{0.65\textwidth}'
    else:
        spec = r'>{\raggedright\arraybackslash}p{0.24\textwidth}r>{\raggedright\arraybackslash}p{0.5\textwidth}'
    lines = ['\n\n\\begin{center}\n\\begin{tabular}{%s}\n\\toprule' % spec]
    for index, (cells, _) in enumerate(rows):
        lines.append(' & '.join(text for text, _ in cells) + r' \\')
        if index == 0 and cells[0][1]:
            lines.append('\\midrule')
    lines.append('\\bottomrule\n\\end{tabular}\n\\end{center}\n')
    return '\n'.join(lines)


def figure_tex(figure):
    images = figure['images']
    if figure['row']:
        width = 0.96 / len(images)
        body = '\\hfill'.join('\\includegraphics[width=%.3f\\textwidth]{figures/%s.pdf}' % (width, name)
                              for name in images)
    else:
        width = 0.7 if figure['narrow'] else (0.8 if images[0].startswith('c_') else 1.0)
        body = '\\includegraphics[width=%.2f\\textwidth]{figures/%s.pdf}' % (width, images[0])
    return ('\n\n\\begin{figure}[H]\n\\centering\n%s\n\\caption{%s}\n\\end{figure}\n'
            % (body, figure['caption']))


def main():
    html = open(os.path.join(HERE, 'voronoi_method.html'), encoding='utf-8').read()
    html = re.sub(r'src="data:[^"]*"', '', html)   # the embedded drawings are not needed here
    converter = Converter()
    converter.feed(html)
    body = tidy(''.join(converter.out).replace('$$', ''))
    body = re.sub(r'\n{3,}', '\n\n', body)
    tex = PREAMBLE % (converter.title, converter.abstract) + body + '\n' + ENDING
    for name in ('stress_method.tex',):
        with open(os.path.join(HERE, name), 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(tex)
    print('%d equations, %d characters -> docs/stress_method.tex' % (converter.equation, len(tex)))
    left = sorted(set(ch for ch in tex if ord(ch) > 127))
    print('characters LaTeX may not know:', left or 'none')


if __name__ == '__main__':
    main()

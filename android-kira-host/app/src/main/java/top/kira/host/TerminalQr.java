package top.kira.host;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Decodes terminal cells as pixels; no QR payload is read or regenerated. */
final class TerminalQr {
    static final class Matrix {
        final int size;
        private final boolean[][] dark;
        Matrix(boolean[][] dark) { this.dark = dark; size = dark.length; }
        boolean dark(int x, int y) { return dark[y][x]; }
    }

    static final class Part {
        final String text;
        final Matrix qr;
        Part(String text, Matrix qr) { this.text = text; this.qr = qr; }
    }

    private static final class Line {
        final String text;
        final boolean[] upper, lower;
        final boolean candidate;
        Line(String text, boolean[] upper, boolean[] lower, boolean candidate) {
            this.text = text; this.upper = upper; this.lower = lower; this.candidate = candidate;
        }
    }

    private static final class Style {
        boolean foreground = true, background, inverse, explicitBackground;
        void reset() { foreground = true; background = false; inverse = false; explicitBackground = false; }
        void sgr(String values) {
            String[] fields = values.isEmpty() ? new String[]{"0"} : values.split(";", -1);
            for (int index = 0; index < fields.length; index++) {
                int code = number(fields[index]);
                if (code == 0) reset();
                else if (code == 7) inverse = true;
                else if (code == 27) inverse = false;
                else if (code == 30 || code == 90) foreground = false;
                else if (code >= 31 && code <= 37 || code >= 91 && code <= 97 || code == 39) foreground = true;
                else if (code == 40 || code == 100) { background = false; explicitBackground = true; }
                else if (code >= 41 && code <= 47 || code >= 101 && code <= 107) { background = true; explicitBackground = true; }
                else if (code == 49) { background = false; explicitBackground = false; }
                else if (code == 38 || code == 48) {
                    boolean light;
                    if (index + 4 < fields.length && number(fields[index + 1]) == 2) {
                        int red = number(fields[index + 2]), green = number(fields[index + 3]), blue = number(fields[index + 4]);
                        light = 299L * red + 587L * green + 114L * blue >= 128000;
                        index += 4;
                    } else if (index + 2 < fields.length && number(fields[index + 1]) == 5) {
                        int palette = number(fields[index + 2]);
                        light = palette < 16 ? palette != 0 && palette != 8
                            : palette >= 232 ? palette >= 244
                            : ((palette - 16) / 36 + (palette - 16) / 6 % 6 + (palette - 16) % 6) >= 8;
                        index += 2;
                    } else continue;
                    if (code == 38) foreground = light;
                    else { background = light; explicitBackground = true; }
                }
            }
        }
    }

    static List<Part> parse(String value) {
        if (value == null || value.isEmpty()) return Collections.singletonList(new Part("", null));
        ArrayList<Part> result = new ArrayList<>();
        ArrayList<Line> run = new ArrayList<>();
        StringBuilder prose = new StringBuilder();
        Style style = new Style();
        String[] lines = value.replace("\r\n", "\n").replace('\r', '\n').split("\n", -1);
        for (int index = 0; index < lines.length; index++) {
            Line line = line(lines[index], style);
            if (line.candidate && (run.isEmpty() || run.get(0).upper.length == line.upper.length) && run.size() < 256) {
                run.add(line);
            } else {
                flush(run, prose, result);
                if (line.candidate) run.add(line);
                else prose.append(line.text).append(index + 1 < lines.length ? "\n" : "");
            }
        }
        flush(run, prose, result);
        if (prose.length() > 0 || result.isEmpty()) result.add(new Part(prose.toString(), null));
        return result;
    }

    private static void flush(List<Line> run, StringBuilder prose, List<Part> parts) {
        if (run.isEmpty()) return;
        Matrix matrix = decode(run);
        if (matrix != null) {
            if (prose.length() > 0) { parts.add(new Part(prose.toString(), null)); prose.setLength(0); }
            parts.add(new Part(null, matrix));
        } else {
            for (Line row : run) prose.append(row.text).append('\n');
        }
        run.clear();
    }

    private static Line line(String raw, Style style) {
        StringBuilder plain = new StringBuilder();
        ArrayList<Boolean> upper = new ArrayList<>(), lower = new ArrayList<>();
        boolean compatible = true, visibleBlock = false, coloredBackground = false;
        for (int index = 0; index < raw.length();) {
            char current = raw.charAt(index++);
            if (current == 27) {
                if (index < raw.length() && raw.charAt(index) == '[') {
                    int start = ++index;
                    while (index < raw.length() && !(raw.charAt(index) >= '@' && raw.charAt(index) <= '~')) index++;
                    if (index < raw.length()) {
                        char end = raw.charAt(index++);
                        if (end == 'm') style.sgr(raw.substring(start, index - 1));
                    }
                } else if (index < raw.length() && raw.charAt(index) == ']') {
                    index++;
                    while (index < raw.length()) {
                        if (raw.charAt(index++) == 7) break;
                        if (index >= 2 && raw.charAt(index - 2) == 27 && raw.charAt(index - 1) == '\\') break;
                    }
                } else if (index < raw.length()) index++;
                continue;
            }
            if (current < 32 && current != '\t') continue;
            plain.append(current);
            boolean foreground = style.inverse ? style.background : style.foreground;
            boolean background = style.inverse ? style.foreground : style.background;
            boolean top = background, bottom = background;
            if (current == '\u2588') { top = foreground; bottom = foreground; visibleBlock = true; }
            else if (current == '\u2580') { top = foreground; visibleBlock = true; }
            else if (current == '\u2584') { bottom = foreground; visibleBlock = true; }
            else if (current != ' ') compatible = false;
            coloredBackground |= style.explicitBackground;
            upper.add(!top); lower.add(!bottom);
        }
        boolean[] a = new boolean[upper.size()], b = new boolean[lower.size()];
        for (int index = 0; index < a.length; index++) { a[index] = upper.get(index); b[index] = lower.get(index); }
        return new Line(plain.toString(), a, b, compatible && a.length >= 21 && a.length <= 512 && (visibleBlock || coloredBackground));
    }

    private static Matrix decode(List<Line> lines) {
        if (lines.size() < 10) return null;
        int width = lines.get(0).upper.length;
        boolean[][] raw = new boolean[lines.size() * 2][width];
        for (int index = 0; index < lines.size(); index++) {
            raw[index * 2] = lines.get(index).upper;
            raw[index * 2 + 1] = lines.get(index).lower;
        }
        int top = 0, bottom = raw.length, left = 0, right = width;
        while (top < bottom && uniformRow(raw, top, left, right)) top++;
        while (bottom > top && uniformRow(raw, bottom - 1, left, right)) bottom--;
        while (left < right && uniformColumn(raw, left, top, bottom)) left++;
        while (right > left && uniformColumn(raw, right - 1, top, bottom)) right--;
        int height = bottom - top;
        width = right - left;
        if (width < 21 || height < 21) return null;
        for (int size = 21; size <= 177; size += 4) {
            if (width % size != 0 || height % size != 0) continue;
            int scaleX = width / size, scaleY = height / size;
            if (scaleX < 1 || scaleX > 4 || scaleY < 1 || scaleY > 4) continue;
            boolean[][] modules = new boolean[size][size];
            boolean consistent = true;
            for (int y = 0; y < height && consistent; y++) for (int x = 0; x < width; x++) {
                boolean expected = raw[top + y / scaleY * scaleY][left + x / scaleX * scaleX];
                if (raw[top + y][left + x] != expected) { consistent = false; break; }
                modules[y / scaleY][x / scaleX] = expected;
            }
            if (!consistent) continue;
            int score = finderScore(modules);
            if (score == 147 || score == 0) {
                if (score == 0) for (int y = 0; y < size; y++) for (int x = 0; x < size; x++) modules[y][x] = !modules[y][x];
                return new Matrix(modules);
            }
        }
        return null;
    }

    private static int finderScore(boolean[][] cells) {
        int score = 0, size = cells.length;
        int[][] corners = {{0, 0}, {size - 7, 0}, {0, size - 7}};
        for (int[] corner : corners) for (int y = 0; y < 7; y++) for (int x = 0; x < 7; x++) {
            boolean expected = x == 0 || y == 0 || x == 6 || y == 6 || (x >= 2 && x <= 4 && y >= 2 && y <= 4);
            if (cells[corner[1] + y][corner[0] + x] == expected) score++;
        }
        return score;
    }

    private static boolean uniformRow(boolean[][] cells, int y, int left, int right) {
        for (int x = left + 1; x < right; x++) if (cells[y][x] != cells[y][left]) return false;
        return right > left;
    }
    private static boolean uniformColumn(boolean[][] cells, int x, int top, int bottom) {
        for (int y = top + 1; y < bottom; y++) if (cells[y][x] != cells[top][x]) return false;
        return bottom > top;
    }
    private static int number(String text) {
        try { return text.isEmpty() ? 0 : Integer.parseInt(text); }
        catch (NumberFormatException ignored) { return -1; }
    }
}

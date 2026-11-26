"""Prepare a private shared-nginx candidate and diff. Never publish or reload."""
import argparse
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    punctuation: bool = False


@dataclass(frozen=True)
class Node:
    head: tuple
    start: int
    end: int
    children: tuple | None = None
    body_start: int | None = None


def tokens(text):
    result = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text[index] == '#':
            end = text.find('\n', index)
            index = len(text) if end < 0 else end + 1
            continue
        start = index
        if text[index] in '{};':
            result.append(Token(text[index], index, index + 1, True))
            index += 1
            continue
        value = ''
        while index < len(text) and not text[index].isspace() and text[index] not in '{};#':
            char = text[index]
            if char in ('"', "'"):
                delimiter = char
                index += 1
                while index < len(text) and text[index] != delimiter:
                    if text[index] == '\\':
                        if index + 1 >= len(text):
                            raise ValueError('Unfinished quoted escape')
                        value += text[index:index + 2]
                        index += 2
                    else:
                        value += text[index]
                        index += 1
                if index >= len(text):
                    raise ValueError('Unfinished quote')
                index += 1
            elif char == '\\':
                if index + 1 >= len(text):
                    raise ValueError('Unfinished escape')
                value += text[index:index + 2]
                index += 2
            elif text.startswith('${', index):
                end = text.find('}', index + 2)
                if end < 0:
                    raise ValueError('Unfinished variable')
                value += text[index:end + 1]
                index = end + 1
            else:
                value += char
                index += 1
        result.append(Token(value, start, index))
    return result


def parse(text):
    stream = tokens(text)
    def block(index, nested):
        nodes = []
        while index < len(stream):
            if stream[index].punctuation and stream[index].value == '}':
                if not nested:
                    raise ValueError('Unexpected closing brace')
                return tuple(nodes), index + 1, stream[index].end
            start = stream[index].start
            head = []
            while index < len(stream) and not stream[index].punctuation:
                head.append(stream[index].value)
                index += 1
            if not head or index == len(stream) or stream[index].value == '}':
                raise ValueError('Unfinished or empty nginx directive')
            delimiter = stream[index]
            if delimiter.value == ';':
                nodes.append(Node(tuple(head), start, delimiter.end))
                index += 1
            else:
                children, index, end = block(index + 1, True)
                nodes.append(Node(tuple(head), start, end, children, delimiter.end))
        if nested:
            raise ValueError('Unclosed nginx block')
        return tuple(nodes), index, len(text)
    return block(0, False)[0]


def one(nodes, name):
    selected = [n for n in nodes if n.head == (name,) and n.children is not None]
    if len(selected) != 1:
        raise ValueError('Expected exactly one ' + name + ' block')
    return selected[0]


def location(node):
    if node.head[0] != 'location':
        return False
    args = node.head[1:]
    if len(args) == 1:
        path = args[0]
    elif len(args) == 2 and args[0] in ('=', '^~'):
        path = args[1]
    else:
        if any('/travel' in value or '@travel' in value for value in args):
            raise ValueError('Ambiguous regex or named travel location')
        return False
    if path.startswith('@travel'):
        raise ValueError('Named travel locations need explicit review')
    return path == '/travel' or path.startswith('/travel/')


def zone(node):
    if node.head[0] != 'limit_req_zone':
        return None
    matches = [v.split(':', 1)[0][5:] for v in node.head[1:] if v.startswith('zone=')]
    if len(matches) != 1:
        raise ValueError('Invalid request zone definition')
    return matches[0]


def variable(node):
    return node.head[-1] if node.head[0] == 'map' else None


def size(value):
    match = re.fullmatch(r'(\d+)([kKmM]?)', value)
    if not match:
        raise ValueError('Map bucket must be a literal size')
    return int(match[1]) * {'': 1, 'k': 1024, 'm': 1024 * 1024}[match[2].lower()]


def signature(nodes, excluded=()):
    excluded = set(excluded)
    return [(n.head, None if n.children is None else signature(n.children, excluded)) for n in nodes if n.start not in excluded]


def all_nodes(nodes):
    for node in nodes:
        yield node
        if node.children is not None:
            yield from all_nodes(node.children)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def section(text, label, context, selected):
    begin = list(re.finditer(r'(?m)^[ \t]*# BEGIN TRAVEL ' + label + r'\r?\n', text))
    end = list(re.finditer(r'(?m)^[ \t]*# END TRAVEL ' + label + r'(?:\r?\n|$)', text))
    if not begin and not end:
        return [(n.start, n.end, '') for n in selected]
    if len(begin) != 1 or len(end) != 1 or begin[0].end() >= end[0].start():
        raise ValueError('Ambiguous managed markers')
    start, finish = begin[0].start(), end[0].end()
    if not (context.body_start <= start < finish <= context.end):
        raise ValueError('Managed section is outside its context')
    if any(not start <= n.start < n.end <= finish for n in selected):
        raise ValueError('Managed definitions also occur outside markers')
    if any(start <= n.start < finish and n not in selected for n in context.children):
        raise ValueError('Unrelated directive inside managed section')
    if not text[context.body_start:start].strip():
        start = context.body_start
    return [(start, finish, '')]


def replace(text, edits):
    # Co-located insertions come before deletions, so a fresh block may replace
    # a managed block at the exact same offset without losing its surroundings.
    edits = sorted(edits, key=lambda edit: (edit[0], edit[1]))
    result, cursor = [], 0
    for start, end, value in edits:
        if start < cursor or end < start:
            raise ValueError('Overlapping nginx edits')
        result.extend((text[cursor:start], value))
        cursor = end
    result.append(text[cursor:])
    return ''.join(result)


def managed(text, label):
    body = '\n'.join('    ' + line if line else '' for line in text.strip().splitlines())
    return '\n    # BEGIN TRAVEL ' + label + '\n' + body + '\n    # END TRAVEL ' + label + '\n'


def merge(source, template):
    http_text, server_text = template.split('# ---- 放在 server 块内', 1)
    http_text = http_text.split('# ---- 放在 http 块内', 1)[1].split('\n', 1)[1]
    server_text = server_text.split('\n', 1)[1]
    incoming = parse(http_text)
    new_locations = parse(server_text)
    if not incoming or not new_locations or not all(location(n) for n in new_locations):
        raise ValueError('Template contains unsupported server directives')
    if any(n.head[0] not in ('map', 'limit_req_zone', 'map_hash_bucket_size') for n in incoming):
        raise ValueError('Template contains unsupported http directives')
    zones = {zone(n) for n in incoming if zone(n)}
    variables = {variable(n) for n in incoming if variable(n)}
    original = parse(source)
    # An unseen include could reference one of the replaced shared zones/maps.
    # Only the standard MIME table is permitted by this bounded merge workflow.
    if any(n.head[0] == 'include' and n.head not in (('include', 'mime.types'), ('include', '/etc/nginx/mime.types')) for n in all_nodes(original)):
        raise ValueError('Included config requires independent review before merging')
    http = one(original, 'http')
    servers = [n for n in http.children if n.head == ('server',) and n.children is not None]
    targets = [n for n in servers if any(location(child) for child in n.children)]
    if len(targets) != 1:
        raise ValueError('Expected one server containing travel locations')
    server = targets[0]
    old_locations = [n for n in server.children if location(n)]
    buckets = [n for n in http.children if n.head[0] == 'map_hash_bucket_size']
    required_buckets = [n for n in incoming if n.head[0] == 'map_hash_bucket_size']
    if len(buckets) > 1 or len(required_buckets) != 1:
        raise ValueError('Ambiguous map bucket setting')
    required = size(required_buckets[0].head[1])
    previous = size(buckets[0].head[1]) if buckets else None
    bucket = max(required, previous or 0)
    http_text = replace(http_text, [(required_buckets[0].start, required_buckets[0].end, f'map_hash_bucket_size {bucket};')])
    old_http = [n for n in http.children if n in buckets or zone(n) in zones or variable(n) in variables]
    if any(sum(zone(n) == name for n in old_http) > 1 for name in zones) or any(sum(variable(n) == name for n in old_http) > 1 for name in variables):
        raise ValueError('Duplicate travel zone or map definitions')
    excluded = {n.start for n in old_http + old_locations}
    remaining = signature(original, excluded)
    encoded_remaining = json.dumps(remaining, ensure_ascii=False)
    for name in zones:
        if re.search(r'zone=' + re.escape(name) + r'(?=[:\s"\\]|$)', encoded_remaining):
            raise ValueError('Travel zone is referenced outside travel locations: ' + name)
    for name in variables:
        if re.search(re.escape(name) + r'\b|\$\{' + re.escape(name[1:]) + r'\}', encoded_remaining):
            raise ValueError('Travel variable is referenced outside travel locations: ' + name)
    edits = section(source, 'HTTP', http, old_http) + section(source, 'SERVER', server, old_locations)
    edits += [(http.body_start, http.body_start, managed(http_text, 'HTTP')),
              (server.body_start, server.body_start, managed(server_text, 'SERVER'))]
    candidate = replace(source, edits)
    parsed = parse(candidate)
    new_http = one(parsed, 'http')
    new_server = [n for n in new_http.children if n.head == ('server',) and any(location(child) for child in n.children or ())]
    if len(new_server) != 1:
        raise ValueError('Merged server identity changed')
    final_locations = [n for n in new_server[0].children if location(n)]
    final_http = [n for n in new_http.children if n.head[0] == 'map_hash_bucket_size' or zone(n) in zones or variable(n) in variables]
    if signature(final_locations) != signature(new_locations) or signature(final_http) != signature(parse(http_text)):
        raise ValueError('Merged travel directives differ from template')
    after = signature(parsed, {n.start for n in final_locations + final_http})
    if remaining != after:
        raise ValueError('Unrelated nginx directives changed')
    return candidate, {'unrelated_directives_unchanged': True, 'standard_mime_include_only': True, 'unrelated_directives_sha256': sha(encoded_remaining),
                       'replaced_locations': [list(n.head) for n in old_locations], 'new_location_count': len(final_locations),
                       'zone_names': sorted(zones), 'map_bucket_before': previous, 'map_bucket_after': bucket}


def prepare(source_file, template_file, output):
    source_file, template_file, output = map(lambda p: Path(p).absolute(), (source_file, template_file, output))
    if any(p != p.resolve() for p in (source_file, template_file, output)):
        raise ValueError('Use canonical paths without symlinks')
    if source_file.is_relative_to(output) or template_file.is_relative_to(output):
        raise ValueError('Output overlaps inputs')
    source = source_file.read_bytes().decode('utf-8')
    template = template_file.read_bytes().decode('utf-8')
    candidate, report = merge(source, template)
    again, _ = merge(candidate, template)
    if again != candidate:
        raise ValueError('Merge is not idempotent')
    report.update({'source_sha256': sha(source), 'template_sha256': sha(template), 'candidate_sha256': sha(candidate),
                   'idempotent': True, 'syntax_tested': False, 'production_deployed': False})
    diff = ''.join(difflib.unified_diff(source.splitlines(keepends=True), candidate.splitlines(keepends=True), fromfile='production-before.conf', tofile='nginx-candidate.conf'))
    output.mkdir(mode=0o700)
    for name, data in (('nginx-candidate.conf', candidate), ('nginx.diff', diff), ('report.json', json.dumps(report, ensure_ascii=False, indent=2))):
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(data)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', required=True)
    parser.add_argument('--template', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    try:
        report = prepare(args.source_file, args.template, args.output_dir)
    except (OSError, ValueError, IndexError) as error:
        parser.exit(1, f'Nginx merge refused: {error}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

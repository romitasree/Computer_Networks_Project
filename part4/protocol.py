"""Parse and format tracker protocol control lines.

Functions return dictionaries when parsing and strings when formatting.
Formatted strings include angle brackets and a trailing LF.
Networking callers must assemble complete lines and encode outgoing strings.
This module does not perform socket I/O or calculate file checksums.
"""

from ipaddress import IPv4Address
import re

LINE_MAX = 2048
UINT64_MAX = (1 << 64) - 1


def _token(value, limit=255):
    if not isinstance(value, str) or not value:
        raise ValueError("Expected a nonempty text field")
    if len(value.encode("utf-8")) > limit:
        raise ValueError("Field is too long")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127
           or c in "<>" for c in value):
        raise ValueError("Field contains whitespace or framing characters")
    return value


def _filename(value):
    _token(value)
    if value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError("Filename must be a basename")
    return value


def _md5(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", value):
        raise ValueError("MD5 must contain 32 hexadecimal characters")
    return value


def _number(value, minimum=0, maximum=UINT64_MAX):
    if isinstance(value, bool):
        raise ValueError("Boolean values are not protocol numbers")
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9]+", value):
            raise ValueError("Expected an unsigned decimal integer")
        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError("Protocol integer is too large") from exc
    if not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Integer must be between {minimum} and {maximum}")
    return value


def _address(value):
    _token(value, 15)
    try:
        IPv4Address(value)
    except ValueError as exc:
        raise ValueError("Invalid IPv4 address") from exc
    return value


def _line_tokens(line, framed):
    if not isinstance(line, str):
        raise ValueError("Expected a text line")
    if len(line.encode("utf-8")) >= LINE_MAX:
        raise ValueError("Protocol line is too long")

    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]

    if "\n" in line or "\r" in line:
        raise ValueError("Expected exactly one protocol line")

    if not framed:
        line = line.strip(" \t")

    if line.startswith("<") and line.endswith(">"):
        line = line[1:-1]
    elif framed or "<" in line or ">" in line:
        raise ValueError("Invalid protocol framing")

    if "<" in line or ">" in line:
        raise ValueError("Unexpected framing character")
    if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in line):
        raise ValueError("Invalid control character")

    tokens = re.split(r"[ \t]+", line.strip(" \t"))
    if tokens == [""]:
        raise ValueError("Empty protocol line")
    return tokens


def _wire(parts):
    result = "<" + " ".join(str(p) for p in parts) + ">\n"
    if len(result.encode("utf-8")) >= LINE_MAX:
        raise ValueError("Formatted protocol line is too long")
    return result


def _status(value):
    if value not in ("succ", "fail", "ferr"):
        raise ValueError("Invalid reply status")
    return value


def protocol_format_request(request):
    """Format a request dictionary as one wire line."""
    if not isinstance(request, dict):
        raise ValueError("Expected a request dictionary")

    try:
        kind = request["type"]

        if kind == "list":
            parts = ["REQ", "LIST"]

        elif kind == "get":
            filename = _filename(request["filename"])
            if not filename.endswith(".track") or len(filename) <= 6:
                raise ValueError("GET requires a filename ending in .track")
            parts = ["GET", filename]

        elif kind == "create":
            parts = [
                "createtracker",
                _filename(request["filename"]),
                _number(request["filesize"]),
                _token(request["description"]),
                _md5(request["md5"]),
                _address(request["ip"]),
                _number(request["port"], 1, 65535),
            ]

        elif kind == "update":
            start = _number(request["start_byte"])
            end = _number(request["end_byte"])
            if start > end:
                raise ValueError("Start byte cannot exceed end byte")
            parts = [
                "updatetracker",
                _filename(request["filename"]),
                start,
                end,
                _address(request["ip"]),
                _number(request["port"], 1, 65535),
            ]

        else:
            raise ValueError("Unknown request type")

    except KeyError as exc:
        raise ValueError(f"Missing request field: {exc.args[0]}") from exc

    return _wire(parts)


def protocol_parse_request(line):
    """Parse a framed wire request or an unframed manual command."""
    t = _line_tokens(line, framed=False)

    if t == ["REQ", "LIST"]:
        result = {"type": "list"}

    elif len(t) == 2 and t[0] == "GET":
        result = {"type": "get", "filename": t[1]}

    elif len(t) == 7 and t[0] == "createtracker":
        result = {
            "type": "create",
            "filename": t[1],
            "filesize": _number(t[2]),
            "description": t[3],
            "md5": t[4],
            "ip": t[5],
            "port": _number(t[6], 1, 65535),
        }

    elif len(t) == 6 and t[0] == "updatetracker":
        result = {
            "type": "update",
            "filename": t[1],
            "start_byte": _number(t[2]),
            "end_byte": _number(t[3]),
            "ip": t[4],
            "port": _number(t[5], 1, 65535),
        }

    else:
        raise ValueError("Unknown request or incorrect field count")

    protocol_format_request(result)
    return result


def protocol_format_reply(reply):
    """Format one reply control line, not a complete LIST/GET response."""
    if not isinstance(reply, dict):
        raise ValueError("Expected a reply dictionary")

    try:
        kind = reply["type"]

        if kind == "create":
            parts = ["createtracker", _status(reply["status"])]

        elif kind == "update":
            parts = [
                "updatetracker",
                _filename(reply["filename"]),
                _status(reply["status"]),
            ]

        elif kind == "list_begin":
            parts = ["REP", "LIST", _number(reply["number"])]

        elif kind == "list_item":
            parts = [
                _number(reply["number"], 1),
                _filename(reply["filename"]),
                _number(reply["filesize"]),
                _md5(reply["md5"]),
            ]

        elif kind == "list_end":
            parts = ["REP", "LIST", "END"]

        elif kind == "get_begin":
            parts = ["REP", "GET", "BEGIN"]

        elif kind == "get_end":
            parts = ["REP", "GET", "END", _md5(reply["md5"])]

        else:
            raise ValueError("Unknown reply type")

    except KeyError as exc:
        raise ValueError(f"Missing reply field: {exc.args[0]}") from exc

    return _wire(parts)


def protocol_parse_reply(line):
    """Parse one framed reply control line."""
    t = _line_tokens(line, framed=True)

    if len(t) == 2 and t[0] == "createtracker":
        result = {"type": "create", "status": t[1]}

    elif len(t) == 3 and t[0] == "updatetracker":
        result = {"type": "update", "filename": t[1], "status": t[2]}

    elif t == ["REP", "LIST", "END"]:
        result = {"type": "list_end"}

    elif len(t) == 3 and t[:2] == ["REP", "LIST"]:
        result = {"type": "list_begin", "number": _number(t[2])}

    elif t == ["REP", "GET", "BEGIN"]:
        result = {"type": "get_begin"}

    elif len(t) == 4 and t[:3] == ["REP", "GET", "END"]:
        result = {"type": "get_end", "md5": t[3]}

    elif len(t) == 4 and re.fullmatch(r"[0-9]+", t[0]):
        result = {
            "type": "list_item",
            "number": _number(t[0], 1),
            "filename": t[1],
            "filesize": _number(t[2]),
            "md5": t[3],
        }

    else:
        raise ValueError("Unknown reply or incorrect field count")

    protocol_format_reply(result)
    return result

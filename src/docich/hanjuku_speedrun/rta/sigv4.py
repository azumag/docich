"""AWS Signature Version 4 (header based) for S3-compatible object storage.

Cloudflare R2 speaks the S3 API: region ``auto``, service ``s3`` and
path-style requests ``/<bucket>/<key>``.  This module implements only the
parts the archive job needs -- ``PutObject``, ``HeadObject`` and
``CopyObject`` -- with the standard library (``hashlib``, ``hmac``,
``http.client``, ``urllib.parse``).

The payload digest is computed by streaming the file once; the file is then
streamed again in the request body.  Nothing here reads a whole object into
memory, so a several-hundred-megabyte log file is fine.

The endpoint is ``https://<account>.r2.cloudflarestorage.com`` by default.
A configured endpoint may be ``http://127.0.0.1:<port>`` only for an explicit
test setting on a loopback host; every other plain-``http`` endpoint is
rejected.

No credential value is ever put in an exception message or a log line; errors
carry a fixed code only.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import ssl
import stat
from urllib.parse import parse_qsl, quote, urlsplit

ALGORITHM = 'AWS4-HMAC-SHA256'
DEFAULT_REGION = 'auto'
DEFAULT_SERVICE = 's3'
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
CHUNK_SIZE = 1024 * 1024
#: A response body is never shown; read at most this much so a stray error
#: page cannot fill memory.
MAX_RESPONSE_BODY = 64 * 1024
DEFAULT_TIMEOUT = 60.0
LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


class SigV4Error(Exception):
    """A fixed-code signing/transport failure (never contains credentials)."""

    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


class _BodyChanged(Exception):
    """The file shrank while its body was being sent (internal sentinel)."""


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str
    region: str = DEFAULT_REGION
    service: str = DEFAULT_SERVICE


@dataclass(frozen=True)
class Endpoint:
    scheme: str
    host: str
    port: int
    netloc: str

    @property
    def is_https(self):
        return self.scheme == 'https'


# ---------------------------------------------------------------------------
# URI / header canonicalisation
# ---------------------------------------------------------------------------


def uri_encode(value):
    """RFC 3986 encoding used by SigV4 (unreserved characters stay)."""
    return quote(str(value), safe='-_.~')


def encode_path(path):
    """Encode each path segment, keeping the ``/`` separators."""
    text = str(path)
    if not text.startswith('/'):
        text = '/' + text
    return '/' + '/'.join(uri_encode(segment) for segment in text[1:].split('/'))


def canonical_query(query):
    """Sorted, encoded query string.

    ``query`` may be a mapping, a sequence of pairs, or an already encoded
    query string.  An empty query is an empty string (not ``None``).
    """
    if query is None:
        return ''
    if isinstance(query, str):
        text = query[1:] if query.startswith('?') else query
        if not text:
            return ''
        pairs = parse_qsl(text, keep_blank_values=True)
    elif isinstance(query, dict):
        pairs = list(query.items())
    else:
        pairs = list(query)
    encoded = sorted(
        (uri_encode(key), uri_encode(value)) for key, value in pairs)
    return '&'.join(f'{key}={value}' for key, value in encoded)


def canonical_headers(headers):
    """Return ``(canonical_headers, signed_headers)`` for a mapping.

    Header names are lower cased and sorted; values are stripped and runs of
    whitespace are collapsed.  The returned header block ends with a newline,
    as the canonical request requires a blank line before the signed header
    list.
    """
    selected = {}
    for name, value in (headers or {}).items():
        if value is None:
            continue
        key = str(name).strip().lower()
        if not key:
            continue
        selected[key] = ' '.join(str(value).strip().split())
    names = sorted(selected)
    block = ''.join(f'{name}:{selected[name]}\n' for name in names)
    return block, ';'.join(names)


def canonical_request(method, path, query, headers, payload_hash):
    """Build the SigV4 canonical request string.

    ``path`` is the *raw* path (``object_path`` output); it is encoded exactly
    once here.  Callers must not pass an already percent-encoded path.
    """
    block, signed = canonical_headers(headers)
    return '\n'.join([
        str(method).upper(),
        encode_path(path),
        canonical_query(query),
        block,
        signed,
        str(payload_hash),
    ])


def string_to_sign(amz_date, scope, canonical):
    """The SigV4 string to sign."""
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return f'{ALGORITHM}\n{amz_date}\n{scope}\n{digest}'


def scope_for(amz_date, region=DEFAULT_REGION, service=DEFAULT_SERVICE):
    """``<date>/<region>/<service>/aws4_request``."""
    day = str(amz_date)[:8]
    return f'{day}/{region}/{service}/aws4_request'


def signing_key(secret_key, amz_date, region=DEFAULT_REGION,
                service=DEFAULT_SERVICE):
    """Derived signing key bytes.

    The four HMAC steps are exposed as separate calls in the tests, so a
    mistake in one step is distinguishable from a mistake in the canonical
    request or the final signature.
    """
    day = str(amz_date)[:8]
    key = ('AWS4' + str(secret_key)).encode('utf-8')
    for part in (day, region, service, 'aws4_request'):
        key = hmac.new(key, str(part).encode('utf-8'), hashlib.sha256).digest()
    return key


def sign(key, string):
    """One HMAC-SHA256 signature over the string to sign."""
    return hmac.new(key, str(string).encode('utf-8'), hashlib.sha256).hexdigest()


def signature(key, string):
    """Alias for :func:`sign` (the final SigV4 signature step)."""
    return sign(key, string)


def authorization(credentials, amz_date, signed_headers, signature):
    """The ``Authorization`` header value."""
    scope = scope_for(amz_date, credentials.region, credentials.service)
    return (f'{ALGORITHM} Credential={credentials.access_key}/{scope}, '
            f'SignedHeaders={signed_headers}, Signature={signature}')


def amz_date(now=None):
    """``YYYYMMDDTHHMMSSZ`` in UTC."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def sign_headers(credentials, method, path, headers, payload_hash, *,
                 query='', now=None):
    """Return a copy of ``headers`` plus ``x-amz-date`` and ``Authorization``.

    ``headers`` must include ``host``.  ``x-amz-content-sha256`` is added when
    missing.  The caller sends exactly the returned headers.
    """
    if not isinstance(credentials, Credentials):
        raise SigV4Error('invalid_credentials')
    if not credentials.access_key or not credentials.secret_key:
        raise SigV4Error('invalid_credentials')
    prepared = {str(name).lower(): str(value) for name, value in headers.items()
                if value is not None}
    if not prepared.get('host'):
        raise SigV4Error('missing_host')
    stamp = amz_date(now)
    prepared.setdefault('x-amz-date', stamp)
    prepared.setdefault('x-amz-content-sha256', payload_hash)
    canonical = canonical_request(
        method, path, query, prepared, payload_hash)
    scope = scope_for(prepared['x-amz-date'], credentials.region,
                      credentials.service)
    string = string_to_sign(prepared['x-amz-date'], scope, canonical)
    key = signing_key(credentials.secret_key, prepared['x-amz-date'],
                      credentials.region, credentials.service)
    signature = sign(key, string)
    _block, signed = canonical_headers(prepared)
    prepared['authorization'] = authorization(
        credentials, prepared['x-amz-date'], signed, signature)
    return prepared


# ---------------------------------------------------------------------------
# streaming payload digest
# ---------------------------------------------------------------------------


def _hash_stream(stream, limit=None, *, chunk_size=CHUNK_SIZE):
    """Hash at most ``limit`` bytes (all bytes when ``limit`` is None).

    Reading exactly the size that was advertised in ``Content-Length`` keeps
    the payload digest and the body in agreement even when another writer
    appends to the file after the size was measured.  A short read raises
    :class:`_BodyChanged`.
    """
    digest = hashlib.sha256()
    remaining = limit
    while remaining is None or remaining > 0:
        wanted = chunk_size if remaining is None else min(chunk_size, remaining)
        block = stream.read(wanted)
        if not block:
            if remaining is not None and remaining > 0:
                raise _BodyChanged()
            break
        digest.update(block)
        if remaining is not None:
            remaining -= len(block)
    return digest.hexdigest()


def hash_file(path, *, chunk_size=CHUNK_SIZE):
    """Stream a regular file and return its hex SHA-256.

    ``O_NOFOLLOW`` keeps a symlink from redirecting the read; the archive
    layer already lists only regular files, and this is the second guard.
    """
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise SigV4Error('file_unreadable') from None
    try:
        with os.fdopen(fd, 'rb') as stream:
            try:
                return _hash_stream(stream, chunk_size=chunk_size)
            except _BodyChanged:
                raise SigV4Error('file_unreadable') from None
    except SigV4Error:
        raise
    except OSError:
        raise SigV4Error('file_unreadable') from None


class _BoundedReader:
    """File-like wrapper that never yields more than ``limit`` bytes.

    ``http.client`` copies a file object until EOF.  Without the bound a file
    that grows after its size was read would send bytes past
    ``Content-Length`` and desynchronise the connection.
    """

    def __init__(self, stream, limit):
        self._stream = stream
        self._limit = max(0, int(limit))
        self._sent = 0

    def read(self, size=-1):
        remaining = self._limit - self._sent
        if remaining <= 0 or size == 0:
            return b''
        if size is None or size < 0 or size > remaining:
            size = remaining
        block = self._stream.read(size)
        if not block:
            raise _BodyChanged()
        self._sent += len(block)
        return block


# ---------------------------------------------------------------------------
# endpoint / HTTP
# ---------------------------------------------------------------------------


def parse_endpoint(value, *, explicit=False):
    """Parse the R2 endpoint and reject unsafe plain-HTTP settings."""
    text = str(value or '').strip()
    if not text:
        raise SigV4Error('invalid_endpoint')
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in {'http', 'https'}:
        raise SigV4Error('invalid_endpoint')
    if parts.username or parts.password:
        raise SigV4Error('invalid_endpoint')
    if parts.query or parts.fragment:
        raise SigV4Error('invalid_endpoint')
    if parts.path not in ('', '/'):
        raise SigV4Error('invalid_endpoint')
    host = parts.hostname
    if not host:
        raise SigV4Error('invalid_endpoint')
    if scheme != 'https':
        if not explicit or host not in LOOPBACK_HOSTS:
            raise SigV4Error('insecure_endpoint')
    try:
        port = parts.port or (443 if scheme == 'https' else 80)
    except ValueError:
        raise SigV4Error('invalid_endpoint') from None
    if port <= 0 or port > 65535:
        raise SigV4Error('invalid_endpoint')
    netloc = parts.netloc
    return Endpoint(scheme=scheme, host=host, port=port, netloc=netloc)


def object_path(bucket, key):
    """Raw path-style ``/<bucket>/<key>``.

    The path is deliberately *not* encoded here.  SigV4 canonicalises the
    raw path once and the request target is encoded from the same raw value,
    so a key can never be percent-encoded twice (``%`` -> ``%25``).
    """
    bucket = str(bucket or '')
    key = str(key or '').lstrip('/')
    if not bucket or not key:
        raise SigV4Error('invalid_object_key')
    return f'/{bucket}/{key}'


class R2Client:
    """Minimal S3-compatible client for one bucket.

    Every method raises :class:`SigV4Error` with a fixed code on failure; the
    response body is never exposed.
    """

    def __init__(self, endpoint, credentials, bucket, *, timeout=DEFAULT_TIMEOUT):
        if not isinstance(endpoint, Endpoint):
            raise SigV4Error('invalid_endpoint')
        self.endpoint = endpoint
        self.credentials = credentials
        self.bucket = str(bucket or '')
        if not self.bucket:
            raise SigV4Error('invalid_bucket')
        self.timeout = timeout

    def _connection(self):
        if self.endpoint.is_https:
            context = ssl.create_default_context()
            return http.client.HTTPSConnection(
                self.endpoint.host, self.endpoint.port,
                timeout=self.timeout, context=context)
        return http.client.HTTPConnection(
            self.endpoint.host, self.endpoint.port, timeout=self.timeout)

    def _send(self, method, key, *, body=None, payload_hash=EMPTY_SHA256,
              extra_headers=None):
        raw_path = object_path(self.bucket, key)
        # Sign the raw path (canonical_request encodes it once) and send the
        # same path encoded once.  Encoding the already-encoded path again is
        # what broke keys containing '%' or non-ASCII characters.
        request_path = encode_path(raw_path)
        headers = {
            'host': self.endpoint.netloc,
            'x-amz-content-sha256': payload_hash,
        }
        headers.update(extra_headers or {})
        signed = sign_headers(
            self.credentials, method, raw_path, headers, payload_hash)
        connection = self._connection()
        try:
            connection.request(method, request_path, body=body, headers=signed)
            response = connection.getresponse()
            try:
                response.read(MAX_RESPONSE_BODY)
            except (OSError, http.client.HTTPException):
                pass
            return response.status, response.getheader('Content-Length')
        except (OSError, http.client.HTTPException):
            raise SigV4Error('network') from None
        finally:
            connection.close()

    def put_file(self, key, path, *, content_type='application/octet-stream'):
        """Upload one file with a streamed body; returns its byte size.

        The digest and the body come from the same open descriptor.  The body
        is bounded to the size measured at open time, so a file that grows
        while it is being sent cannot push bytes past ``Content-Length`` (the
        archive layer notices the growth and reports ``local_changed``).
        """
        path = Path(path)
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            raise SigV4Error('file_unreadable') from None
        try:
            try:
                meta = os.fstat(fd)
            except OSError:
                raise SigV4Error('file_unreadable') from None
            if not stat.S_ISREG(meta.st_mode):
                raise SigV4Error('file_unreadable')
            size = meta.st_size
            with os.fdopen(fd, 'rb') as stream:
                fd = -1
                try:
                    digest = _hash_stream(stream, size)
                    stream.seek(0)
                    body = _BoundedReader(stream, size)
                except (OSError, _BodyChanged):
                    raise SigV4Error('local_changed') from None
                headers = {
                    'content-length': str(size),
                    'content-type': content_type,
                }
                try:
                    status, _length = self._send(
                        'PUT', key, body=body, payload_hash=digest,
                        extra_headers=headers)
                except _BodyChanged:
                    raise SigV4Error('local_changed') from None
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if status not in (200, 201, 204):
            raise SigV4Error(f'http_{status}')
        return size

    def head_size(self, key):
        """Remote object size, or ``None`` when it does not exist."""
        status, length = self._send('HEAD', key)
        if status == 404:
            return None
        if status != 200:
            raise SigV4Error(f'http_{status}')
        try:
            return int(length)
        except (TypeError, ValueError):
            raise SigV4Error('invalid_content_length') from None

    #: S3 API style names (kept as thin aliases for callers that use them).
    put_object = put_file
    head_object = head_size

    def copy_object(self, source_key, destination_key):
        """Server-side copy; returns ``True`` on success."""
        # ``x-amz-copy-source`` is a header value, so it carries its own
        # once-encoded path (the destination goes through ``_send``).
        source = encode_path(object_path(self.bucket, source_key))
        status, _length = self._send(
            'PUT', destination_key,
            extra_headers={'x-amz-copy-source': source,
                           'content-length': '0'})
        if status not in (200, 201):
            raise SigV4Error(f'http_{status}')
        return True

"""SigV4 signing tests for the speedrun R2 archive.

The first two cases are the official AWS documentation vectors (IAM
``ListUsers`` and the S3 ``GET`` example); the remaining tests exercise each
signing stage independently so a regression points at one step.
"""
from __future__ import annotations

import hashlib
import hmac
import pytest

from docich.hanjuku_speedrun.rta import sigv4


IAM_ACCESS = 'AKIDEXAMPLE'
IAM_SECRET = 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY'
IAM_DATE = '20150830T123600Z'
IAM_CANONICAL_HASH = 'f536975d06c0309214f805bb90ccff089219ecd68b2577efef23edd43b7e1a59'
IAM_STRING_TO_SIGN = (
    'AWS4-HMAC-SHA256\n'
    '20150830T123600Z\n'
    '20150830/us-east-1/iam/aws4_request\n'
    f'{IAM_CANONICAL_HASH}'
)
IAM_SIGNATURE = '5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7'

S3_SECRET = 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'
S3_DATE = '20130524T000000Z'
S3_SIGNATURE = 'f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41'


def test_official_aws_iam_list_users_vector():
    headers = {
        'content-type': 'application/x-www-form-urlencoded; charset=utf-8',
        'host': 'iam.amazonaws.com',
        'x-amz-date': IAM_DATE,
    }
    canonical = sigv4.canonical_request(
        'GET', '/', 'Action=ListUsers&Version=2010-05-08', headers,
        sigv4.EMPTY_SHA256)
    assert hashlib.sha256(canonical.encode('utf-8')).hexdigest() == IAM_CANONICAL_HASH

    scope = sigv4.scope_for(IAM_DATE, 'us-east-1', 'iam')
    string = sigv4.string_to_sign(IAM_DATE, scope, canonical)
    assert string == IAM_STRING_TO_SIGN

    key = sigv4.signing_key(IAM_SECRET, IAM_DATE, 'us-east-1', 'iam')
    assert sigv4.sign(key, string) == IAM_SIGNATURE


def test_official_aws_s3_get_vector():
    headers = {
        'host': 'examplebucket.s3.amazonaws.com',
        'range': 'bytes=0-9',
        'x-amz-content-sha256': sigv4.EMPTY_SHA256,
        'x-amz-date': S3_DATE,
    }
    canonical = sigv4.canonical_request(
        'GET', '/test.txt', '', headers, sigv4.EMPTY_SHA256)
    assert hashlib.sha256(canonical.encode('utf-8')).hexdigest() == (
        '7344ae5b7ee6c3e7e6b0fe0640412a37625d1fbfff95c48bbb2dc43964946972')
    key = sigv4.signing_key(S3_SECRET, S3_DATE, 'us-east-1', 's3')
    string = sigv4.string_to_sign(
        S3_DATE, sigv4.scope_for(S3_DATE, 'us-east-1', 's3'), canonical)
    assert sigv4.sign(key, string) == S3_SIGNATURE


def test_signing_key_derivation_matches_an_independent_hmac_chain():
    secret = 'test-secret-value'
    date = '20260101T000000Z'
    expected = ('AWS4' + secret).encode('utf-8')
    for part in ('20260101', 'auto', 's3', 'aws4_request'):
        expected = hmac.new(expected, part.encode('utf-8'),
                            hashlib.sha256).digest()
    assert sigv4.signing_key(secret, date, 'auto', 's3') == expected
    # Every intermediate step is one plain HMAC-SHA256.
    day_key = hmac.new(('AWS4' + secret).encode('utf-8'), b'20260101',
                       hashlib.sha256).digest()
    assert len(day_key) == 32


def test_sign_headers_shape_and_signed_header_list():
    credentials = sigv4.Credentials('ACCESSKEYEXAMPLE', 'SECRETKEYEXAMPLE')
    headers = {'host': '127.0.0.1:9000',
               'x-amz-content-sha256': sigv4.EMPTY_SHA256}
    signed = sigv4.sign_headers(
        credentials, 'PUT', '/bucket/key.txt', headers, sigv4.EMPTY_SHA256,
        now=__import__('datetime').datetime(
            2026, 1, 2, 3, 4, 5, tzinfo=__import__('datetime').timezone.utc))
    assert signed['x-amz-date'] == '20260102T030405Z'
    assert signed['x-amz-content-sha256'] == sigv4.EMPTY_SHA256
    authorization = signed['authorization']
    assert authorization.startswith(
        'AWS4-HMAC-SHA256 Credential=ACCESSKEYEXAMPLE/20260102/auto/s3/aws4_request, ')
    assert 'SignedHeaders=host;x-amz-content-sha256;x-amz-date' in authorization
    signature = authorization.rsplit('Signature=', 1)[1]
    assert len(signature) == 64
    assert 'SECRETKEYEXAMPLE' not in authorization


def test_payload_hash_is_streamed_and_matches_hashlib(tmp_path):
    path = tmp_path / 'log.bin'
    path.write_bytes(b'0123456789' * 1000)
    assert sigv4.hash_file(path, chunk_size=7) == hashlib.sha256(
        path.read_bytes()).hexdigest()


def test_hash_file_rejects_a_symlink(tmp_path):
    real = tmp_path / 'real'
    real.write_bytes(b'x')
    link = tmp_path / 'link'
    link.symlink_to(real)
    with pytest.raises(sigv4.SigV4Error) as caught:
        sigv4.hash_file(link)
    assert caught.value.code == 'file_unreadable'


def test_path_encoding_and_canonical_query():
    assert sigv4.encode_path('/bucket/a b/ü.txt') == '/bucket/a%20b/%C3%BC.txt'
    assert sigv4.encode_path('/bucket/') == '/bucket/'
    assert sigv4.canonical_query({'b': '2', 'a': '1 2'}) == 'a=1%202&b=2'
    assert sigv4.canonical_query('') == ''
    assert sigv4.canonical_query(None) == ''
    # The raw path is encoded once by canonical_request / the request target;
    # object_path must not pre-encode it.
    assert sigv4.object_path('bucket', 'a b/ü+:.txt') == '/bucket/a b/ü+:.txt'


def test_bounded_reader_never_sends_past_the_advertised_size(tmp_path):
    path = tmp_path / 'grows.bin'
    original = b'0123456789'
    path.write_bytes(original)
    with path.open('rb') as stream:
        reader = sigv4._BoundedReader(stream, len(original))
        with path.open('ab') as writer:
            writer.write(b'extra bytes appended during the upload')
        chunks = []
        while True:
            chunk = reader.read(3)
            if not chunk:
                break
            chunks.append(chunk)
    assert b''.join(chunks) == original


def test_put_file_streams_exactly_the_measured_size(tmp_path):
    path = tmp_path / 'grows-during-put.bin'
    original = b'abcdefghij'
    path.write_bytes(original)
    endpoint = sigv4.parse_endpoint('https://account.r2.cloudflarestorage.com')
    client = sigv4.R2Client(
        endpoint, sigv4.Credentials('ACCESS', 'SECRET'), 'bucket')
    sent = {}

    class _Response:
        status = 200

        def read(self, _limit):
            return b''

        def getheader(self, _name):
            return str(len(original))

    class _Connection:
        def request(self, method, target, body=None, headers=None):
            # Another writer appends after the size was measured: the body
            # reader must still stop at Content-Length.
            with path.open('ab') as stream:
                stream.write(b'appended')
            sent['body'] = b''.join(iter(lambda: body.read(2), b''))

        def getresponse(self):
            return _Response()

        def close(self):
            pass

    client._connection = lambda: _Connection()
    assert client.put_file('some/key', path) == len(original)
    assert sent['body'] == original


def test_endpoint_validation():
    https = sigv4.parse_endpoint('https://account.r2.cloudflarestorage.com')
    assert https.is_https and https.host == 'account.r2.cloudflarestorage.com'
    loopback = sigv4.parse_endpoint('http://127.0.0.1:8765', explicit=True)
    assert loopback.host == '127.0.0.1' and loopback.port == 8765
    with pytest.raises(sigv4.SigV4Error):
        # Plain HTTP is only a test setting.
        sigv4.parse_endpoint('http://127.0.0.1:8765')
    with pytest.raises(sigv4.SigV4Error):
        sigv4.parse_endpoint('http://example.com', explicit=True)
    with pytest.raises(sigv4.SigV4Error):
        sigv4.parse_endpoint('https://user:pass@example.com')
    with pytest.raises(sigv4.SigV4Error):
        sigv4.parse_endpoint('https://example.com/path')

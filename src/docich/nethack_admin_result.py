"""Public fixed-schema result for the private NetHack check/release helpers."""
import json
import re
import sys

REFUSALS = {name: 81 + i for i, name in enumerate((
    "evidence_unproven", "resources_unproven", "resources_present", "process_identity_reused",
    "process_coverage_unproven", "process_snapshot_changed", "context_changed",
    "approval_required", "approval_expired", "fingerprint_changed", "busy",
    "audit_unproven", "persistence_unconfirmed", "code_unverified"))}


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def public_result(raw, mode, sha, code):
    result = {'status': 'refused', 'reason': 'gateway_result_unverified', 'history_authority': False}
    try:
        value = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(value, dict) or not re.fullmatch('[0-9a-f]{40}', sha) or type(code) is not int:
            raise ValueError()
        if mode == 'check':
            if code != 0 or value.get('status') != 'diagnosed' or value.get('sha') != sha:
                raise ValueError()
            proof = value['diagnostics']['nethack_admin_check']
            if (type(proof.get('schema_version')) is not int or proof['schema_version'] != 1
                    or proof.get('history_authority') is not False):
                raise ValueError()
            if proof.get('status') == 'admin-eligible':
                if (proof.get('all_resources_released') is not True or proof.get('code_sha') != sha
                        or not isinstance(proof.get('fingerprint'), str)
                        or not re.fullmatch('[0-9a-f]{64}', proof['fingerprint'])
                        or type(proof.get('expires_at')) is not int or proof['expires_at'] % 600):
                    raise ValueError()
                return {k: proof[k] for k in ('schema_version', 'status', 'history_authority',
                        'all_resources_released', 'fingerprint', 'expires_at', 'code_sha')}, 0
            if proof.get('status') == 'refused' and proof.get('reason') in REFUSALS:
                result['reason'] = proof['reason']
        elif mode == 'release':
            rc = value.get('exit_code')
            if (value.get('status') != 'executed' or value.get('sha') != sha
                    or value.get('output') != 'withheld' or type(rc) is not int or rc != code):
                raise ValueError()
            if code == 0:
                return {'status': 'admin-released-or-already-committed', 'history_authority': False}, 0
            result['reason'] = {v: k for k, v in REFUSALS.items()}.get(code,
                {25: 'code_unverified', 64: 'approval_required', 124: 'resources_unproven'}.get(code, 'evidence_unproven'))
    except Exception:
        pass
    return result, 1


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        raw, mode, sha, rc = args
        code = int(rc)
    except Exception:
        raw, mode, sha, code = '', '', '', -1
    result, code = public_result(raw, mode, sha, code)
    print(json.dumps(result, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())

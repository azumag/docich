"""Pure Hanjuku run identity validation shared by data and observation layers."""

def run_identity(raw):
    if (not isinstance(raw, dict) or raw.get('game') != 'hanjuku-hero'
            or type(raw.get('generation')) is not int or raw['generation'] <= 0
            or any(not isinstance(raw.get(k), str) or not 1 <= len(raw[k]) <= 256
                   for k in ('runtime_id', 'lease_id'))):
        return None
    return {k: raw[k] for k in ('game', 'runtime_id', 'generation', 'lease_id')}

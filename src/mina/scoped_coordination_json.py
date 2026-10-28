"""協調原本の厳密JSON。重複key・非有限値・不正UTF8を許可しない。"""
import json


def strict_json(raw,limit=1048576):
    if len(raw)>limit:raise ValueError('scoped_coordination_payload_too_large')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('scoped_coordination_duplicate_key')
            result[key]=value
        return result
    def invalid(_):raise ValueError('scoped_coordination_nonfinite_json')
    return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=invalid)

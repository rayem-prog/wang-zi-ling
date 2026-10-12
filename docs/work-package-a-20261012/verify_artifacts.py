"""Validate work package A documentation artifacts (requires existing jsonschema)."""
import json
from pathlib import Path
import re
from jsonschema import Draft202012Validator, FormatChecker

root = Path(__file__).parent
schema = json.loads((root/'contract.schema.json').read_text())
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema, format_checker=FormatChecker())
examples = json.loads((root/'contract-examples.json').read_text())
assert set(examples) == {'valid','updating','failed','first_start','normal_empty','risk_forbids_buy','revoked','filter_no_match'}
for name, x in examples.items():
    validator.validate(x)
    assert x['counts']['filtered_total'] == len(x['rows']), name
    assert sum(x['counts']['brackets'].values()) == len(x['rows']), name
    assert x['counts']['snapshot_total'] >= len(x['rows']), name
    assert len({r['code'] for r in x['rows']}) == len(x['rows']), name
    for bracket, total in x['counts']['brackets'].items():
        assert total == sum(r['price_bracket']==bracket for r in x['rows']), name
    for row in x['rows']:
        price=row['price']; bracket='low' if price<=20 else 'mid' if price<=50 else 'high' if price<=100 else 'top'
        assert row['price_bracket']==bracket, name
    for action, status in x['action_permissions'].items():
        reasons=[r for r in x['blocked_reasons'] if action in r['actions']]
        assert bool(reasons)==(status!='allowed'), (name,action)
    if x['result_state']=='none':
        assert all(x[k] is None for k in ['snapshot_id','data_as_of','generated_at']), name
        assert not x['rows'] and x['empty_kind']=='no_snapshot', name
    else:
        assert all(x[k] for k in ['snapshot_id','data_as_of','generated_at']), name
        assert all(x['versions'].values()), name
    if x['result_state']=='revoked':
        assert not x['rows'] and all(v=='forbidden' for v in x['action_permissions'].values()), name
    if x['result_state']=='historical':
        assert all(x['action_permissions'][a]!='allowed' for a in ['create_buy','add_position','export_orders']), name
    if x['job']['status'] in ('failed','interrupted'):
        assert x['job']['error'] and x['job']['finished_at'], name
    previous=None
    for stage in x['selection']['funnel']:
        assert stage['input']>=stage['output'], name
        assert sum(stage['excluded_by_reason'].values()) == stage['input']-stage['output'], name
        if previous is not None: assert stage['input']==previous, name
        previous=stage['output']
    if x['empty_kind']=='screened_out':
        assert x['result_state']=='current' and x['counts']['snapshot_total']==0 and previous==0, name
    print('PASS contract example:',name)

e=json.loads((root/'baseline-evidence.json').read_text())
assert e['scenarios']['valid']['rows']==5 and e['scenarios']['expired_audit']['rows']==0
assert e['scenarios']['normal_empty']['ready'] is False
assert e['scenarios']['registry_restart']['previous_job_found'] is False
assert e['real_signature_stable'] and e['real_signature_stable_after_http']
assert e['scenarios']['risk_forbids_buy']['weights']==[0.]*5
for t in e['timings'].values():
    if isinstance(t,dict): assert t['n']==100 and t['min_ms']<=t['p50_ms']<=t['p95_ms']<=t['max_ms']
print('PASS baseline evidence invariants')
for file in root.glob('*.md'):
    content=file.read_text()
    assert not re.search(r'\b(?:TODO|TBD)\b',content), file
    assert sum(line.startswith('```') for line in content.splitlines())%2==0, file
    for link in re.findall(r'\]\(([^)]+)\)',content):
        if not link.startswith(('http','/','#')): assert (root/link.split('#')[0]).exists(), (file,link)
print('PASS local document links, code fences and placeholder scan')
content=(root/'design.md').read_text()
assert all(f'F{i:02}' in content for i in range(1,8))
assert all(f'A{i:02}' in content for i in range(1,17))
print('PASS coverage: 7 fault scenarios, 16 acceptance scenarios')
print('8 contract examples validated; design artifacts verified; application implementation not performed.')

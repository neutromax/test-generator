import os
import time
from pathlib import Path

import app
from src.vio_client import VIOClient

repo = Path('d:/test-generator-master/test-generator-master/src/repositories/flask')
if not repo.is_dir():
    raise SystemExit(f'Repository not found: {repo}')

endpoint = os.getenv('VIO_API_BASE', 'https://vio.automotive-wan.com:446')
client = VIOClient(base_url=endpoint, timeout=120)

if not client.is_configured:
    raise SystemExit('VIO is not configured (missing VIO_API_KEY or VIO_API_BASE).')

# Seed the minimal session state expected by app orchestration functions.
app.st.session_state['vio_client'] = client
app.st.session_state['repository_path'] = str(repo)
app.st.session_state['repository_name'] = repo.name
app.st.session_state['repository_url'] = ''

selected_types = ['unit_test', 'integration_test']

start_err = app.run_vio_orchestration(
    selected_types=selected_types,
    parallel=True,
    max_components=10,
    mode=app.VIO_MODE_RECOMMENDED,
)
if start_err:
    raise SystemExit(f'Could not start run: {start_err}')

run_state = app.st.session_state.get('vio_run_state') or {}
last_done = -1
while run_state.get('active'):
    done = run_state.get('done', 0)
    total = run_state.get('total', 0)
    phase = run_state.get('phase', '')
    if done != last_done:
        print(f'progress: {done}/{total} phase={phase}')
        last_done = done
    time.sleep(2)

run_state = app.st.session_state.get('vio_run_state') or {}
models = app.st.session_state.get('models') or {}
master = app.AGENTS['master']
workers = [d for a, d in models.items() if a != master and 'passed' in d]

passed = sum(d.get('passed', 0) for d in workers)
failed = sum(d.get('failed', 0) for d in workers)
errors = sum(d.get('errors', 0) for d in workers)

print('--- RUN SUMMARY ---')
print('finished:', run_state.get('finished'))
print('error:', run_state.get('error', ''))
print('cancelled:', run_state.get('cancelled', False))
print('totals:', {'passed': passed, 'failed': failed, 'errors': errors})

cov = run_state.get('coverage') or {}
if cov and not cov.get('error'):
    print('coverage_percent:', cov.get('percent'))
    print('coverage_target_met:', cov.get('target_met'))
else:
    print('coverage_error:', cov.get('error', 'none'))

# Show up to 10 failing components.
fail_rows = []
for a, d in models.items():
    if a == master:
        continue
    for c in d.get('components', []):
        if c.get('status') != 'COMPLETED':
            fail_rows.append((c.get('component'), c.get('error') or c.get('failure_stage') or 'failed'))
print('failed_components_count:', len(fail_rows))
for comp, reason in fail_rows[:10]:
    print('FAILED:', comp, '|', str(reason)[:180])

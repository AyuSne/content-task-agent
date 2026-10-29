import json

import pytest


@pytest.mark.parametrize('method', ['oneshot', 'fixed', 'enhanced', 'all'])
def test_legacy_experiment_rejects_before_creating_task_or_files(cli_env, tmp_path, method):
    import os
    import subprocess
    import sys

    from conftest import REPO_ROOT
    case = tmp_path / 'case.json'
    output = tmp_path / 'output'
    data = tmp_path / 'comparison-data'
    env = dict(os.environ, CTA_DATA_DIR=str(data), PYTHONPATH=str(REPO_ROOT / 'src'), PYTHONUTF8='1')
    result = subprocess.run([sys.executable, '-m', 'cta.comparison', '--mode', 'mock', '--method', method,
                             '--case', str(case), '--output', str(output)], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True, encoding='utf-8', check=False)
    assert result.returncode == 4
    response = json.loads(result.stdout)
    assert response['error'] == 'legacy_experiment' and response['method'] == method
    assert '836aa827' in response['message']
    assert not case.exists() and not output.exists() and not data.exists()

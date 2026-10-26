import yaml
from pathlib import Path

# 加载指定路径的yaml文件，生成对应的字典
def load_yaml(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

# .yaml .yml
_yaml_path = Path(__file__).parents[1] / 'prompt'/ 'prompts.yaml'
_yaml_dict = load_yaml(_yaml_path)

# 暴露主agent和subagents的配置
main_config = _yaml_dict['main_agent']
sub_agents_config = _yaml_dict['sub_agents']
# print(f"主智能体配置:{main_config}")
# print(f"子智能体配置:{sub_agents_config}")
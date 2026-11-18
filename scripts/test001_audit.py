"""TEST-001 A0：只读调用审计，尚无实现。"""
def read_command_lines(raw):
    raise NotImplementedError('A0 未实现：需要保留行顺序及 shell read 转义')
def judge_commands(raw):
    raise NotImplementedError('A0 未实现：不执行 judge 的静态基线读取')
def load_inputs(root):
    raise NotImplementedError('A0 未实现：冻结输入校验')
def audit(data, lock):
    raise NotImplementedError('A0 未实现：镜像调用与 judge 差异')

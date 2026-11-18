"""分析器的两类退出原因：输入错误与发布失败。"""


class InputError(ValueError):
    """调用参数、配置身份或输入包不合法；应在执行前退出 2。"""


class PublishError(RuntimeError):
    """报告目录不能完整发布；退出 1，且不能留下可消费的完成清单。"""

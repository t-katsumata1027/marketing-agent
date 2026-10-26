"""外部工程内の限定再試行にも、呼出し元の期限・版検査を継承する。"""
from contextvars import ContextVar
guard=ContextVar('mina_external_attempt_guard',default=None)
normal_retry=ContextVar('mina_normal_read_only_retry',default=False)
managed=ContextVar('mina_managed_external_operation',default=False)


def managed_call(operation):
    """予約単位の置換・救済では、内側の通常AI再試行を必ず無効にする。"""
    token=managed.set(True)
    try:return operation()
    finally:managed.reset(token)

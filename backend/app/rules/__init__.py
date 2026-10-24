"""Rules package. Schema-v2 ops live in state_ops.py and are registered into engine.CHANGE_OPS here, after both
modules are loaded (state_ops imports engine, so engine cannot import state_ops at load time)."""
from . import engine as _engine
from . import state_ops as _state_ops

_state_ops.register(_engine.CHANGE_OPS)

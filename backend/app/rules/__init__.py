"""Rules package. Schema-v2 ops (state_ops.py), Carousel ops (carousel_ops.py) and movement ops (travel_ops.py) and story ops (story_ops.py) are
registered into engine.CHANGE_OPS here, after the modules are loaded (they import engine, so engine cannot import them at load time)."""
from . import carousel_ops as _carousel_ops
from . import engine as _engine
from . import state_ops as _state_ops
from . import story_ops as _story_ops
from . import travel_ops as _travel_ops

_state_ops.register(_engine.CHANGE_OPS)
_carousel_ops.register(_engine.CHANGE_OPS)
_travel_ops.register(_engine.CHANGE_OPS)
_story_ops.register(_engine.CHANGE_OPS)

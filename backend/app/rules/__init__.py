"""Rules package. Schema-v2 ops (state_ops.py), Carousel ops (carousel_ops.py) and movement ops (travel_ops.py) story ops (story_ops.py), new-game ops (opening_ops.py), venue ops (venue_ops.py), fate ops (fate_ops.py) and resident ops (resident_ops.py) are
registered into engine.CHANGE_OPS here, after the modules are loaded (they import engine, so engine cannot import them at load time)."""
from . import carousel_ops as _carousel_ops
from . import engine as _engine
from . import fate_ops as _fate_ops
from . import opening_ops as _opening_ops
from . import resident_ops as _resident_ops
from . import state_ops as _state_ops
from . import story_ops as _story_ops
from . import travel_ops as _travel_ops
from . import venue_ops as _venue_ops

_state_ops.register(_engine.CHANGE_OPS)
_carousel_ops.register(_engine.CHANGE_OPS)
_travel_ops.register(_engine.CHANGE_OPS)
_story_ops.register(_engine.CHANGE_OPS)
_opening_ops.register(_engine.CHANGE_OPS)
_venue_ops.register(_engine.CHANGE_OPS)
_fate_ops.register(_engine.CHANGE_OPS)
_resident_ops.register(_engine.CHANGE_OPS)

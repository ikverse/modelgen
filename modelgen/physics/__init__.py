"""ModelGen's own small physics engine for characters: spring chains (hair, skirts, coat tails,
accessories) and ragdolls. Plain Python, no dependencies, so the same code runs inside Blender (to
bake secondary motion into every clip), in the host's tests, and as a reference for the viewer's
JavaScript port (modelgen/web/physics.js).
"""

from .engine import Capsule, Chain, Particle, World, ragdoll_world  # noqa: F401

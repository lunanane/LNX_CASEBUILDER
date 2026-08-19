"""hwcase -- parametric enclosures around real hardware.

Pipeline:  parts (YAML)  ->  Scene  ->  resolve()  ->  check()  ->  case.build()
           ->  export.to_svg() / to_dxf() / scene_to_json()
"""

from .case import CaseModel, Layer, build
from .library import PartLibrary, load_scene
from .scene import Issue, Resolved, check, resolve
from .schema import CaseSpec, Material, Part, Placement, Scene

__all__ = [
    "PartLibrary", "load_scene",
    "Part", "Scene", "Placement", "CaseSpec", "Material",
    "resolve", "check", "Resolved", "Issue",
    "build", "CaseModel", "Layer",
]
__version__ = "0.1.0"

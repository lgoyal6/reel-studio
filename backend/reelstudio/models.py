from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Range(StrictModel):
    start: float = Field(default=0, ge=0)
    end: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("The end must be after the start")
        return self


class Gain(Range):
    type: Literal["gain"]
    gain_db: float = Field(ge=-60, le=18)


class Clean(StrictModel):
    type: Literal["clean"]
    strength: float = Field(default=0.5, ge=0, le=1)
    mode: Literal["spectral", "speech"] = "spectral"


class Separate(StrictModel):
    type: Literal["isolate", "remove"]
    target: str = Field(min_length=1, max_length=150)


class StemMix(StrictModel):
    type: Literal["stem_mix"]
    gains: dict[Literal["drums", "vocals", "bass", "other"], float] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def bounded_gains(self):
        if any(not -60 <= value <= 18 for value in self.gains.values()):
            raise ValueError("Stem gains must be between -60 and +18 dB")
        return self


class Speed(Range):
    type: Literal["speed"]
    factor: float = Field(ge=0.25, le=4)
    preserve_pitch: Literal[True] = True


class Pitch(Range):
    type: Literal["pitch"]
    semitones: float = Field(ge=-12, le=12)


class Trim(Range):
    type: Literal["trim", "cut"]
    end: float = Field(gt=0)


class Loop(Range):
    type: Literal["loop"]
    end: float = Field(gt=0)
    count: int = Field(default=2, ge=2, le=8)


class ClipRange(Range):
    end: float = Field(gt=0)


class Reorder(StrictModel):
    type: Literal["reorder"]
    ranges: list[ClipRange] = Field(min_length=1, max_length=16)


class Fade(StrictModel):
    type: Literal["fade"]
    in_seconds: float = Field(default=0.25, ge=0, le=30)
    out_seconds: float = Field(default=0.5, ge=0, le=30)


class Normalize(StrictModel):
    type: Literal["normalize"]
    target_lufs: float = Field(default=-16, ge=-30, le=-9)


class AlignMusic(StrictModel):
    type: Literal["align_music"]
    anchor: float = Field(ge=0)
    music_start: float = Field(default=0, ge=0)
    mode: Literal["beat", "drop", "start"] = "beat"
    gain_db: float = Field(default=-6, ge=-60, le=6)


class ReplaceAudio(Range):
    type: Literal["replace_audio"]
    end: float = Field(gt=0)
    music_start: float = Field(default=0, ge=0)
    gain_db: float = Field(default=0, ge=-60, le=6)


class Duck(StrictModel):
    type: Literal["duck"]
    amount_db: float = Field(default=-12, ge=-36, le=0)


class CinematicAd(StrictModel):
    type: Literal["cinematic_ad"]
    headline: str = Field(min_length=1, max_length=90)
    tagline: str = Field(default="", max_length=140)
    brand: str = Field(min_length=1, max_length=40)
    cta: str = Field(min_length=1, max_length=50)
    duration: Literal[15, 30] = 15
    aspect: Literal["portrait", "landscape"] = "portrait"
    accent: Literal["coral", "teal", "blue"] = "coral"

    @model_validator(mode="after")
    def visible_copy(self):
        if not all(value.strip() for value in (self.headline, self.brand, self.cta)):
            raise ValueError("Add a headline, brand name, and call to action for the ad")
        return self


Operation = Annotated[
    Gain | Clean | Separate | StemMix | Speed | Pitch | Trim | Loop | Reorder | Fade | Normalize | AlignMusic | ReplaceAudio | Duck | CinematicAd,
    Field(discriminator="type"),
]


class EditPlan(StrictModel):
    summary: str = Field(min_length=1, max_length=1200)
    operations: list[Operation] = Field(max_length=12)


class EditRequest(StrictModel):
    prompt: str = Field(default="", max_length=4000)
    selection: Range | None = None
    operations: list[Operation] | None = Field(default=None, min_length=1, max_length=12)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.prompt.strip() and not self.operations:
            raise ValueError("Describe an edit or choose an editing control")
        return self


class CheckoutRequest(StrictModel):
    revision_id: str

from docich.comment.pipeline import *  # noqa: F401,F403
from docich.comment import pipeline as _pipeline  # noqa: F401

# expose common names explicitly
from docich.comment.pipeline import (  # noqa: F401
    CommentIntake,
    run_pipeline,
    build_prompt_for,
    classify_intake,
    stub_generator,
)

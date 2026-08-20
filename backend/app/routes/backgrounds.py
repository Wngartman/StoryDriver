from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from urllib.parse import unquote

from app.services.background_library import (
    add_background,
    list_backgrounds,
    remove_background,
    rename_background,
    resolve_background,
)


router = APIRouter(prefix="/backgrounds", tags=["backgrounds"])


class BackgroundRename(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)


@router.get("")
def backgrounds() -> dict:
    return list_backgrounds()


@router.post("/refresh")
def refresh_backgrounds() -> dict:
    return list_backgrounds()


@router.post("/upload", status_code=201)
async def upload_background(request: Request) -> dict:
    filename = unquote(request.headers.get("x-storydriver-filename", "background"))
    try:
        item = add_background(filename, await request.body())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"item": item, **list_backgrounds()}


@router.patch("/{item_id}")
def update_background(item_id: str, payload: BackgroundRename) -> dict:
    try:
        item = rename_background(item_id, payload.display_name)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Background not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"item": item, **list_backgrounds()}


@router.delete("/{item_id}", status_code=204)
def delete_background(item_id: str) -> Response:
    try:
        remove_background(item_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Background not found.") from error
    return Response(status_code=204)


@router.get("/{item_id}/file")
def background_file(item_id: str) -> FileResponse:
    path = resolve_background(item_id)
    if not path:
        raise HTTPException(status_code=404, detail="Background not found.")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

from fastapi import APIRouter, Depends, File, UploadFile, status
from controlplane_api.exceptions import InvalidOperationException
from controlplane_api.utils import get_logger
from api.domain.dtos.user_dto import (
    CreateUserDto,
    CreateUserResponseDto,
    UpdateUserDto,
    UserDto,
    UserListDto,
    UserProfileImageUpdateDto,
    UserRoleUpdateRequestDto,
    user_from_row,
)
from controlplane_api.permissions import Permission
from api.interfaces.security.role_checker import check_permissions_for_current_role
from api.usecases.file_service import FileService
from api.usecases.user_service import UserService
from api.core.container import get_file_service, get_role_service, get_user_service
from api.infrastructure.security.current_user import CurrentUser
from api.usecases.role_service import RoleService
from api.domain.enum.role import RoleType


logger = get_logger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

_USER_SVC = (
    "Unmounted from the gateway monolith. Same paths are reverse-proxied to "
    "user-service (`/api/v1/users`, `/api/v1/groups`, `/api/v1/organizations`). "
    "This module is leftover code; do not remount it."
)


@router.get(
    "/",
    response_model=UserListDto,
    deprecated=True,
    summary="List users (deprecated)",
    description=_USER_SVC,
)
async def list_users(
    skip: int = 0, limit: int = 10,
    service: UserService = Depends(get_user_service),
    _bool: bool = Depends(check_permissions_for_current_role(required_permissions=[Permission.USER_VIEW_ONLY]))
):
    return await service.list_users(skip=skip, limit=limit)


@router.post(
    "/",
    response_model=CreateUserResponseDto,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
    summary="Create user (deprecated)",
    description=_USER_SVC,
)
async def create_user(
    data: CreateUserDto,
    current_user: CurrentUser,
    service: UserService = Depends(get_user_service),
    role_service: RoleService = Depends(get_role_service),
    _bool: bool = Depends(check_permissions_for_current_role(required_permissions=[Permission.USER_READ_AND_WRITE_ONLY]))
):

    data.tenant_id = current_user.tenant_id
    role_guest = await role_service.find_by_name(name=RoleType.GUEST)
    data.role_id = str(role_guest.id) if role_guest.id else None
    new_user_id = await service.create_user(data)
    return CreateUserResponseDto(id=str(new_user_id))


@router.get(
    "/{user_id}",
    response_model=UserDto,
    deprecated=True,
    summary="Get user (deprecated)",
    description=_USER_SVC,
)
async def get_user(
    user_id: str,
    service: UserService = Depends(get_user_service),
    _bool: bool = Depends(check_permissions_for_current_role(
        required_permissions=[Permission.USER_SELF_READ_AND_WRITE_ONLY],
        allow_self_access=True)
    )
):
    user = await service.get_user_by_id(user_id)
    return user_from_row(user)


@router.put(
    "/{user_id}",
    response_model=UserDto,
    status_code=status.HTTP_200_OK,
    deprecated=True,
    summary="Update user (deprecated)",
    description=_USER_SVC,
)
async def update_user(
    user_id: str,
    data: UpdateUserDto,
    service: UserService = Depends(get_user_service),
    _bool: bool = Depends(check_permissions_for_current_role(
        required_permissions=[Permission.USER_READ_AND_WRITE_ONLY, Permission.USER_SELF_READ_AND_WRITE_ONLY],
        allow_self_access=True)
    )

):
    user = await service.update_user(user_id=user_id, user_data=data)
    if user is None:
        raise InvalidOperationException("User update failed.")
    return user_from_row(user)

@router.get(
    "/profile/{image_key:path}",
    response_model=UserProfileImageUpdateDto,
    deprecated=True,
    summary="Get profile image URL (deprecated)",
    description=_USER_SVC,
)
async def get_profile_image(
    image_key: str,
    _bool: bool = Depends(check_permissions_for_current_role(required_permissions=[Permission.USER_VIEW_ONLY])),
    file_service: FileService = Depends(get_file_service)
):
    try:
        image_url = await file_service.get_file_url(image_key)
        return UserProfileImageUpdateDto(image_url=image_url)
    except Exception as e:
        logger.error(f"Error fetching profile image: {e}")
        return UserProfileImageUpdateDto(image_url="")
    
@router.delete(
    "/{user_id}",
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    summary="Delete user (deprecated)",
    description=_USER_SVC,
)
async def delete_user(
    user_id: str,
    service: UserService = Depends(get_user_service),
    _bool: bool = Depends(check_permissions_for_current_role(required_permissions=[Permission.USER_DELETE_ONLY]))
):
    await service.delete_user(user_id)
    return status.HTTP_202_ACCEPTED




@router.put(
    "/{user_id}/update_profile_picture",
    response_model=UserProfileImageUpdateDto,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    summary="Update profile picture (deprecated)",
    description=_USER_SVC,
)
async def update_profile_picture(
    user_id: str,
    file: UploadFile = File(...),
    _bool: bool = Depends(check_permissions_for_current_role(
        required_permissions=[Permission.USER_READ_AND_WRITE_ONLY, Permission.USER_SELF_READ_AND_WRITE_ONLY],
        allow_self_access=True)),
    user_service: UserService = Depends(get_user_service),
    file_service: FileService = Depends(get_file_service),
):
    await user_service.get_user_by_id(user_id)
    file_location = await file_service.upload_file(file)
    await user_service.update_user(user_id=user_id, user_data=UpdateUserDto(image_url=file_location))
    return UserProfileImageUpdateDto(image_url=file_location)


@router.patch(
    "/{user_id}/assign_role",
    response_model=UserDto,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    summary="Assign user role (deprecated)",
    description=_USER_SVC,
)
async def patch_user(
    user_id: str, role_update: UserRoleUpdateRequestDto,
    _: bool = Depends(check_permissions_for_current_role([Permission.USER_ROLE_ASSIGN_OR_REMOVE_ONLY])),
    user_service: UserService = Depends(get_user_service)

):
    
    exsisting_user = await user_service.get_user_by_id(user_id)
    if str(exsisting_user.role_id or "") == role_update.role_id:
        raise InvalidOperationException("User already has this role assigned")
    
    user = await user_service.update_user(
        user_id=user_id, user_data=UpdateUserDto(role_id=role_update.role_id)
    )
    if user is None:
        raise InvalidOperationException("User update failed.")
    return user_from_row(user)
  


@router.patch(
    "/{user_id}/remove_role",
    response_model=UserDto,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    summary="Remove user role (deprecated)",
    description=_USER_SVC,
)
async def remove_user_role(
     user_id: str, role_update: UserRoleUpdateRequestDto,
    _: bool = Depends(check_permissions_for_current_role([Permission.USER_ROLE_ASSIGN_OR_REMOVE_ONLY])),
    user_service: UserService = Depends(get_user_service)
):
    exsisting_user = await user_service.get_user_by_id(user_id)
    if str(exsisting_user.role_id or "") != role_update.role_id:
        raise InvalidOperationException("User does not have this role assigned")
    
    user = await user_service.update_user(
        user_id=user_id, user_data=UpdateUserDto(role_id=None)
    )
    if user is None:
        raise InvalidOperationException("User update failed.")
    return user_from_row(user)

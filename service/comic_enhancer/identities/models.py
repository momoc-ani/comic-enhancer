from pydantic import BaseModel, Field


class CharacterIdentityEntry(BaseModel):
    """描述配置中一个角色的规范身份和跨站别名。"""

    identity_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)


class WorkIdentityEntry(BaseModel):
    """描述配置中一个作品及其角色身份。"""

    identity_id: str
    title_aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    characters: list[CharacterIdentityEntry] = Field(default_factory=list)

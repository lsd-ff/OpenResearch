from openresearch.config import Settings
from openresearch.models.documents import UploadRecord
from openresearch.storage.repository import Repository
from openresearch.storage.upload_files import UploadFiles, UploadSubmission


class UploadService:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.repository = repository
        self.files = UploadFiles(settings.data_dir / "uploads", settings.upload_max_bytes)

    def create(self, submission: UploadSubmission) -> UploadRecord:
        """流式保存文件后记录归属，元数据失败时清理尚未发布的文件。"""
        record = self.files.write(submission)
        try:
            self.repository.uploads.save(record)
        except BaseException:
            self.files.discard(record)
            raise
        return record

    def content(self, upload_id: str, owner_id: str) -> tuple[UploadRecord, bytes]:
        record = self.repository.uploads.get(upload_id, owner_id)
        return record, self.files.read(record)

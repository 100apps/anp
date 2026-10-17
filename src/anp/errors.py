class BusinessError(Exception):
    def __init__(self, message, status=400, code='BUSINESS_ERROR'):
        self.message, self.status, self.code = message, status, code
        super().__init__(message)

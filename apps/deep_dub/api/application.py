"""业务独立开发入口；正式宿主通过app.toml加载同一API与Worker路由。"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from anp.errors import BusinessError
from apps.deep_dub.api.router import router
from apps.deep_dub.api.worker import router as worker_router

app=FastAPI(title='deep_dub 业务 API')


@app.exception_handler(BusinessError)
async def business_error(request,error):
    return JSONResponse(status_code=error.status,content={'error':{'code':error.code,'message':error.message}})


app.include_router(router)
app.include_router(worker_router)

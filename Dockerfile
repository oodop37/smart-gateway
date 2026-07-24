FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 创建数据目录
RUN mkdir -p /app/data

# 复制源码
COPY . .

# 暴露端口
EXPOSE 8765

# 启动
CMD ["python", "app.py"]
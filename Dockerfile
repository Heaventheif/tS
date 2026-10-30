# صورة Docker اختيارية للنشر على Render (runtime: Docker) أو أي منصة أخرى.
# Render يضبط PORT تلقائيًا عند التشغيل، وserver.py يقرؤه من البيئة.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=10000

WORKDIR /app

# طبقة المتطلبات أولًا للاستفادة من كاش البناء (لا مكتبات إضافية غير المطلوبة)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# المنصة تتجاوز EXPOSE وتضبط PORT ديناميكيًا — الخادم يقرؤه في وقت التشغيل
EXPOSE 10000

CMD ["python", "server.py"]

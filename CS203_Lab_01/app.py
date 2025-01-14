import json
import os
import logging
from flask import Flask, render_template, request, redirect, url_for, flash
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from pythonjsonlogger import jsonlogger
from time import time

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Configure structured JSON logging for better readability and monitoring.
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Initialize Flask app and set a secret key for securely handling sessions and forms.
app = Flask(__name__)
app.secret_key = 'secret'
COURSE_FILE = 'course_catalog.json'

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Configure OpenTelemetry for distributed tracing, using JaegerExporter to send traces to Jaeger.
tracer_provider = TracerProvider(resource=Resource.create({"service.name": "course-catalog-app"}))
trace.set_tracer_provider(tracer_provider)
jaeger_exporter = JaegerExporter(agent_host_name="localhost", agent_port=6831)
tracer_provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
FlaskInstrumentor().instrument_app(app)
tracer = trace.get_tracer(__name__)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Global metrics to track total requests and error counts.
request_counts = {"catalog": 0, "add_course": 0, "course_details": 0}
error_counts = {"add_course": 0, "db_connection": 0}

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Track the number of requests to each route.
@app.before_request
def track_requests():
    route = request.endpoint
    if route in request_counts:
        request_counts[route] += 1
        logger.info(f"Total requests to {route}: {request_counts[route]}")

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Load courses from a JSON file. Returns an empty list if the file is missing.
def load_courses():
    if not os.path.exists(COURSE_FILE):
        return []
    with open(COURSE_FILE, 'r') as file:
        return json.load(file)

# Save new course data into the JSON file, ensuring the catalog is updated.
def save_courses(data):
    courses = load_courses()
    courses.append(data)
    with open(COURSE_FILE, 'w') as file:
        json.dump(courses, file, indent=4)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Render the home page and trace the operation.
@app.route('/')
def index():
    with tracer.start_as_current_span("render_index"):
        logger.info("Rendered index page.")
        return render_template('index.html')

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Render the course catalog page, with trace attributes for course count and user IP.
@app.route('/catalog')
def course_catalog():
    start_time = time()
    with tracer.start_as_current_span("render_course_catalog") as span:
        courses = load_courses()
        span.set_attribute("course.count", len(courses))
        span.set_attribute("user.ip", request.remote_addr)
        logger.info("Rendered course catalog with %d courses.", len(courses))
    duration = time() - start_time
    logger.info(f"Processing time for /catalog: {duration:.2f} seconds")
    return render_template('course_catalog.html', courses=courses)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# View details of a specific course. Redirect to catalog if the course code is not found.
@app.route('/course/<code>')
def course_details(code):
    with tracer.start_as_current_span("browse_course_details") as span:
        span.set_attribute("course.code", code)
        span.set_attribute("user.ip", request.remote_addr)
        courses = load_courses()
        course = next((course for course in courses if course['code'] == code), None)
        if not course:
            logger.error(f"No course found with code {code}")
            flash(f"No course found with code '{code}'.", "danger")
            return redirect(url_for('course_catalog'))
        logger.info(f"Displayed details for course: {course}")
        return render_template('course_details.html', course=course)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Add a new course, validating required fields and saving the course data with trace spans.
@app.route("/add_course", methods=["GET", "POST"])
def add_course():
    if request.method == "POST":
        with tracer.start_as_current_span("add_new_course") as span:
            course_data = {
                'code': request.form['code'],
                'coursename': request.form['coursename'],
                'instructor': request.form['instructor'],
                'semester': request.form['semester'],
                'schedule': request.form['schedule'],
                'classroom': request.form['classroom'],
                'prerequisites': request.form.get('prerequisites', ''),
                'grading': request.form.get('grading', ''),
                'description': request.form.get('description', '')
            }
            span.set_attribute("course.code", course_data['code'])
            span.set_attribute("course.name", course_data['coursename'])

            # Validate required fields
            required_fields = ['coursename', 'instructor']
            missing_fields = [field for field in required_fields if not course_data[field].strip()]
            if missing_fields:
                error_counts["add_course"] += 1
                span.add_event("Validation failed", {"missing_fields": missing_fields})
                logger.error(f"Missing required fields. Error count: {error_counts['add_course']}")
                flash("Some fields were missing. Unsuccessful addition.", "danger")
                return redirect(url_for("course_catalog"))

            # Save the course and log the operation
            try:
                with tracer.start_as_current_span("save_course_data") as save_span:
                    save_courses(course_data)
                    save_span.add_event("Course saved successfully", {"course_code": course_data['code']})
                logger.info(f"Course added: {course_data['coursename']} ({course_data['code']})")
                flash(f"Course '{course_data['coursename']}' added successfully!", "success")
            except Exception as e:
                error_counts["db_connection"] += 1
                logger.error(f"Database error: {e}. Error count: {error_counts['db_connection']}")
                flash("Database error occurred.", "danger")
            return redirect(url_for('course_catalog'))
    return render_template('add_course.html')

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Run the Flask application in debug mode.
if __name__ == "__main__":
    logger.info("Starting Flask application...")
    app.run(debug=True)

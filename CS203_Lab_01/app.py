import json
import os
import logging
from flask import Flask, render_template, request, redirect, url_for, flash
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from pythonjsonlogger import jsonlogger

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# I’m configuring the logger to display structured JSON logs. 
# This helps me read and analyze log outputs more efficiently by organizing them in a clear format.

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = jsonlogger.JsonFormatter()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# I’m initializing the Flask application here.
app = Flask(__name__)
app.secret_key = 'secret'
COURSE_FILE = 'course_catalog.json'

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# Now, I’m setting up OpenTelemetry for distributed tracing to monitor the application’s request flows. 
# By using a TracerProvider and the JaegerExporter, I can capture and send trace data to Jaeger for better visualization of how my app performs.

tracer_provider = TracerProvider(resource=Resource.create({"service.name": "course-catalog-app"}))
trace.set_tracer_provider(tracer_provider)

# I’m configuring the Jaeger Thrift Exporter to transmit trace data to Jaeger. 
# This makes it easier to diagnose performance issues by seeing a complete breakdown of each request’s journey through the app.
jaeger_exporter = JaegerExporter(
    agent_host_name="localhost",  # Default to localhost since Jaeger runs locally.
    agent_port=6831  # This is Jaeger’s default port for receiving Thrift traces.
)
tracer_provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))

console_exporter = ConsoleSpanExporter()
tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))

# To ensure I capture traces for every Flask request, I’m instrumenting the Flask app with OpenTelemetry.
FlaskInstrumentor().instrument_app(app)
tracer = trace.get_tracer(__name__)


# --------------------------------------------------------------------------------------------------------------------------------------------------------
def load_courses():
    if not os.path.exists(COURSE_FILE):
        return []
    with open(COURSE_FILE, 'r') as file:
        return json.load(file)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
def save_courses(data):
    courses = load_courses()
    courses.append(data)
    with open(COURSE_FILE, 'w') as file:
        json.dump(courses, file, indent=4)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# I’m creating a trace span to monitor how long it takes to render the index page, which can help debug any slow-loading issues.
@app.route('/')
def index():
    with tracer.start_as_current_span("render_index"):
        logger.info("Rendered index page.")
        return render_template('index.html')

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# I’m adding trace attributes like the number of courses and the user’s IP address to capture useful context about the request.
@app.route('/catalog')
def course_catalog():
    with tracer.start_as_current_span("render_course_catalog") as span:
        courses = load_courses()
        span.set_attribute("course.count", len(courses))
        span.set_attribute("user.ip", request.remote_addr)
        logger.info("Rendered course catalog with %d courses.", len(courses))
        return render_template('course_catalog.html', courses=courses)

# --------------------------------------------------------------------------------------------------------------------------------------------------------
# In this function, I’ve added a span named `browse_course_details` to trace the process of viewing a specific course's details. 
# Trace attributes like the course code and the user's IP address are captured for better context. 
# If the course doesn't exist, an error is logged, and the user is redirected to the course catalog with a flash message.

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
#In this function, I’ve added a span called `add_new_course` to trace the process of adding a course, capturing attributes like course code and name for better context.
# It includes validation to ensure required fields like course name and instructor are not empty, logging errors if validation fails. 
# Another span, `save_course_data`,confirms when the course is successfully saved, helping to monitor each step of the operation for debugging and performance analysis.

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

            # Check for missing required fields
            required_fields = ['coursename', 'instructor']
            missing_fields = [field for field in required_fields if not course_data[field].strip()]
            if missing_fields:
                span.add_event("Validation failed", {"missing_fields": missing_fields})
                logger.error(f"Missing required fields: {', '.join(missing_fields)}")
                flash("Some fields were missing. Unsuccessful addition", "danger")  # Flashing the message
                return redirect(url_for('course_catalog'))  # Redirect to course catalog

            # Save course data if validation passes
            with tracer.start_as_current_span("save_course_data") as save_span:
                save_courses(course_data)
                save_span.add_event("Course saved successfully", {"course_code": course_data['code']})

            logger.info(f"Course added: {course_data['coursename']} ({course_data['code']})")
            flash(f"Course '{course_data['coursename']}' added successfully!", "success")
            return redirect(url_for('course_catalog'))
    return render_template('add_course.html')
    

# 
if __name__ == "__main__":
    logger.info("Starting Flask application...")
    app.run(debug=True)

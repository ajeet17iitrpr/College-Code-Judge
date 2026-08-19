from __future__ import annotations

import csv
import io
import math
import statistics
import zipfile
from collections import defaultdict
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Problem, Submission, TestCase, User
from ..schemas import ResultEmailRequest
from ..security import AuthContext, get_auth_context, get_exam_setting, require_admin, require_admin_csrf
from ..services.mailer import MailDeliveryError, send_result_email

router = APIRouter(prefix="/api/reports", tags=["reports"])


def choose_submission(items: list[Submission], policy: str) -> Submission | None:
    """Choose one language solution as the official result for a problem.

    There is now one stored submission per student/problem/language. The result
    policy therefore chooses between language solutions, not historical versions.
    """
    if not items:
        return None
    official = next((item for item in items if item.official), None)
    if official:
        return official
    if policy == "LATEST":
        return max(items, key=lambda x: x.created_at)
    if policy == "ADMIN_SELECTED":
        return None
    return max(items, key=lambda x: (x.final_score, x.created_at))


def problem_maximum(problem_id: int, db: Session) -> float:
    return float(
        db.scalar(select(func.coalesce(func.sum(TestCase.points), 0.0)).where(TestCase.problem_id == problem_id))
        or 0.0
    )


def resolve_problems(db: Session, problem_ids: list[int] | None = None) -> list[Problem]:
    if problem_ids:
        unique_ids = list(dict.fromkeys(int(x) for x in problem_ids))
        found = list(db.scalars(select(Problem).where(Problem.id.in_(unique_ids))))
        by_id = {p.id: p for p in found}
        missing = [pid for pid in unique_ids if pid not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"Problem(s) not found: {', '.join(map(str, missing))}")
        return [by_id[pid] for pid in unique_ids]
    return list(db.scalars(select(Problem).where(Problem.is_practice.is_(False)).order_by(Problem.id)))


def parse_problem_ids(value: str | None) -> list[int]:
    if not value:
        return []
    ids: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pid = int(part)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="problem_ids must be comma-separated integers.") from exc
        if pid <= 0:
            raise HTTPException(status_code=400, detail="Problem IDs must be positive integers.")
        ids.append(pid)
    if not ids:
        raise HTTPException(status_code=400, detail="Select at least one question.")
    return list(dict.fromkeys(ids))


def result_rows(user: User, db: Session, problem_ids: list[int] | None = None) -> tuple[list[dict], dict]:
    setting = get_exam_setting(db)
    problems = resolve_problems(db, problem_ids)
    submissions = list(db.scalars(select(Submission).where(Submission.user_id == user.id)))
    grouped: dict[int, list[Submission]] = defaultdict(list)
    for item in submissions:
        grouped[item.problem_id].append(item)

    rows: list[dict] = []
    total = 0.0
    maximum = 0.0
    solved = 0
    for problem in problems:
        max_score = problem_maximum(problem.id, db)
        selected = choose_submission(grouped.get(problem.id, []), setting.official_submission_policy)
        final_score = selected.final_score if selected else 0.0
        total += final_score
        maximum += max_score
        if selected and selected.verdict == "AC":
            solved += 1
        rows.append(
            {
                "problem_id": problem.id,
                "problem": problem.title,
                "submission_id": selected.id if selected else None,
                "language": selected.language if selected else None,
                "automatic_score": selected.score if selected else 0.0,
                "manual_score": selected.manual_score if selected else None,
                "final_score": final_score,
                "maximum": max_score,
                "verdict": selected.verdict if selected else "NOT_ATTEMPTED",
                "remarks": selected.manual_notes if selected else "",
                "submitted_at": selected.created_at.isoformat() + "Z" if selected else None,
            }
        )
    percentage = (total / maximum * 100.0) if maximum else 0.0
    summary = {
        "total": total,
        "maximum": maximum,
        "percentage": percentage,
        "passed": percentage >= setting.passing_percentage,
        "solved": solved,
        "attempted": sum(1 for row in rows if row["submission_id"]),
        "policy": setting.official_submission_policy,
        "published": setting.results_published,
        "exam_name": setting.exam_name,
    }
    return rows, summary


def student_result_payload(user: User, db: Session, problem_ids: list[int]) -> dict:
    rows, summary = result_rows(user, db, problem_ids)
    return {
        "student": {
            "id": user.id,
            "full_name": user.full_name,
            "enrollment_number": user.enrollment_number,
            "email": user.email,
            "active": user.active,
            "status": user.status,
        },
        "rows": rows,
        "summary": summary,
    }


def question_statistics(students: list[User], problems: list[Problem], db: Session) -> list[dict]:
    setting = get_exam_setting(db)
    submissions = list(
        db.scalars(
            select(Submission).where(
                Submission.user_id.in_([u.id for u in students]) if students else Submission.user_id == -1,
                Submission.problem_id.in_([p.id for p in problems]) if problems else Submission.problem_id == -1,
            )
        )
    )
    by_user_problem: dict[tuple[int, int], list[Submission]] = defaultdict(list)
    for item in submissions:
        by_user_problem[(item.user_id, item.problem_id)].append(item)

    output: list[dict] = []
    for problem in problems:
        maximum = problem_maximum(problem.id, db)
        attempted_scores: list[float] = []
        all_scores: list[float] = []
        ac_count = 0
        for user in students:
            selected = choose_submission(by_user_problem.get((user.id, problem.id), []), setting.official_submission_policy)
            score = float(selected.final_score if selected else 0.0)
            all_scores.append(score)
            if selected:
                attempted_scores.append(score)
                if selected.verdict == "AC":
                    ac_count += 1
        attempted = len(attempted_scores)
        enrolled = len(students)
        average = statistics.fmean(attempted_scores) if attempted_scores else 0.0
        median = statistics.median(attempted_scores) if attempted_scores else 0.0
        stddev = statistics.pstdev(attempted_scores) if len(attempted_scores) > 1 else 0.0
        overall_avg = statistics.fmean(all_scores) if all_scores else 0.0
        output.append(
            {
                "problem_id": problem.id,
                "problem": problem.title,
                "maximum": maximum,
                "students": enrolled,
                "attempted": attempted,
                "not_attempted": enrolled - attempted,
                "accepted": ac_count,
                "min": min(attempted_scores) if attempted_scores else 0.0,
                "max": max(attempted_scores) if attempted_scores else 0.0,
                "avg": average,
                "median": median,
                "stddev": stddev,
                "avg_including_not_attempted": overall_avg,
                "avg_percentage": (average / maximum * 100.0) if maximum else 0.0,
            }
        )
    return output


def create_student_pdf(user: User, rows: list[dict], summary: dict) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(settings.institute_name, styles["Title"]),
        Paragraph(summary["exam_name"], styles["Heading2"]),
        Spacer(1, 8),
        Paragraph(f"Student: {user.full_name}", styles["BodyText"]),
        Paragraph(f"Email: {user.email}", styles["BodyText"]),
        Paragraph(f"Enrollment: {user.enrollment_number}", styles["BodyText"]),
        Spacer(1, 12),
    ]
    data = [["Problem", "Language", "Verdict", "Automatic", "Manual", "Final", "Maximum"]]
    for row in rows:
        data.append(
            [
                row["problem"],
                row["language"] or "—",
                row["verdict"],
                f'{row["automatic_score"]:.2f}',
                "—" if row["manual_score"] is None else f'{row["manual_score"]:.2f}',
                f'{row["final_score"]:.2f}',
                f'{row["maximum"]:.2f}',
            ]
        )
    table = Table(data, repeatRows=1, colWidths=[54 * mm, 18 * mm, 25 * mm, 22 * mm, 20 * mm, 18 * mm, 20 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#777777")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend(
        [
            table,
            Spacer(1, 12),
            Paragraph(f'Total: {summary["total"]:.2f} / {summary["maximum"]:.2f}', styles["Heading3"]),
            Paragraph(f'Percentage: {summary["percentage"]:.2f}%', styles["BodyText"]),
            Paragraph(f'Result: {"PASS" if summary["passed"] else "FAIL"}', styles["BodyText"]),
        ]
    )
    doc.build(story)
    return buffer.getvalue()


def overall_matrix(students: list[User], problems: list[Problem], db: Session) -> tuple[list[str], list[list], list[dict]]:
    headers = ["Name", "Enrollment", "Email"] + [f"Q{p.id}: {p.title}" for p in problems] + ["Total", "Maximum", "Percentage", "Result"]
    matrix: list[list] = []
    payloads: list[dict] = []
    ids = [p.id for p in problems]
    for user in students:
        payload = student_result_payload(user, db, ids)
        payloads.append(payload)
        row = [user.full_name, user.enrollment_number, user.email]
        row.extend(round(float(x["final_score"]), 2) for x in payload["rows"])
        row.extend(
            [
                round(float(payload["summary"]["total"]), 2),
                round(float(payload["summary"]["maximum"]), 2),
                round(float(payload["summary"]["percentage"]), 2),
                "PASS" if payload["summary"]["passed"] else "FAIL",
            ]
        )
        matrix.append(row)
    return headers, matrix, payloads


def create_overall_pdf(students: list[User], problems: list[Problem], db: Session) -> bytes:
    headers, matrix, _ = overall_matrix(students, problems, db)
    stats = question_statistics(students, problems, db)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=8 * mm,
        leftMargin=8 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = getSampleStyleSheet()
    story = [Paragraph(settings.institute_name, styles["Title"]), Paragraph("Selected-question result report", styles["Heading2"]), Spacer(1, 8)]
    # Use compact question labels in the PDF, with a legend below.
    compact_headers = ["Name", "Enrollment", "Email"] + [f"Q{p.id}" for p in problems] + ["Total", "Max", "%", "Result"]
    data = [compact_headers] + matrix
    widths = [34 * mm, 24 * mm, 42 * mm] + [15 * mm] * len(problems) + [16 * mm, 16 * mm, 15 * mm, 17 * mm]
    table = Table(data, repeatRows=1, colWidths=widths)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#888888")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e8e8e8")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 6.5),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    story.extend([table, Spacer(1, 10), Paragraph("Question legend", styles["Heading3"])])
    for p in problems:
        story.append(Paragraph(f"Q{p.id}: {p.title}", styles["BodyText"]))
    story.extend([Spacer(1, 10), Paragraph("Question-wise statistics", styles["Heading3"])])
    stats_data = [["Question", "Attempted", "Accepted", "Min", "Max", "Average", "Median", "Std dev", "Avg %"]]
    for st in stats:
        stats_data.append([
            f'Q{st["problem_id"]}', st["attempted"], st["accepted"], f'{st["min"]:.2f}', f'{st["max"]:.2f}',
            f'{st["avg"]:.2f}', f'{st["median"]:.2f}', f'{st["stddev"]:.2f}', f'{st["avg_percentage"]:.2f}%'
        ])
    st = Table(stats_data, repeatRows=1)
    st.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#888888")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e8e8e8")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 7),
    ]))
    story.append(st)
    doc.build(story)
    return buffer.getvalue()


def col_name(index: int) -> str:
    value = index + 1
    name = ""
    while value:
        value, rem = divmod(value - 1, 26)
        name = chr(65 + rem) + name
    return name


def worksheet_xml(rows: list[list]) -> str:
    xml_rows: list[str] = []
    for r_idx, row in enumerate(rows, 1):
        cells: list[str] = []
        for c_idx, value in enumerate(row):
            ref = f"{col_name(c_idx)}{r_idx}"
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = xml_escape(str(value if value is not None else ""))
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
        xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(xml_rows) + '</sheetData></worksheet>'


def create_xlsx(students: list[User], problems: list[Problem], db: Session) -> bytes:
    headers, matrix, _ = overall_matrix(students, problems, db)
    stats = question_statistics(students, problems, db)
    result_rows_xlsx = [headers] + matrix
    stats_headers = ["Question", "Maximum", "Students", "Attempted", "Not attempted", "Accepted", "Min", "Max", "Average", "Median", "Std dev", "Average including not attempted", "Average %"]
    stats_rows = [stats_headers] + [[
        st["problem"], st["maximum"], st["students"], st["attempted"], st["not_attempted"], st["accepted"], st["min"], st["max"], st["avg"], st["median"], st["stddev"], st["avg_including_not_attempted"], st["avg_percentage"]
    ] for st in stats]

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>''')
        z.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        z.writestr("xl/workbook.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Overall Results" sheetId="1" r:id="rId1"/><sheet name="Question Statistics" sheetId="2" r:id="rId2"/></sheets></workbook>''')
        z.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>''')
        z.writestr("xl/worksheets/sheet1.xml", worksheet_xml(result_rows_xlsx))
        z.writestr("xl/worksheets/sheet2.xml", worksheet_xml(stats_rows))
    return out.getvalue()


@router.get("/my")
def my_report(auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> dict:
    setting = get_exam_setting(db)
    if not setting.results_published:
        raise HTTPException(status_code=403, detail="Results have not been published.")
    rows, summary = result_rows(auth.user, db)
    return {
        "student": {"full_name": auth.user.full_name, "enrollment_number": auth.user.enrollment_number, "email": auth.user.email},
        "rows": rows,
        "summary": summary,
    }


@router.get("/my.pdf")
def my_pdf(auth: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> Response:
    setting = get_exam_setting(db)
    if not setting.results_published:
        raise HTTPException(status_code=403, detail="Results have not been published.")
    rows, summary = result_rows(auth.user, db)
    return Response(
        create_student_pdf(auth.user, rows, summary),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{auth.user.enrollment_number}_result.pdf"'},
    )


@router.get("/admin/options")
def admin_result_options(_auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    problems = list(db.scalars(select(Problem).where(Problem.is_practice.is_(False)).order_by(Problem.id)))
    students = list(db.scalars(select(User).where(User.role == "student").order_by(User.enrollment_number)))
    return {
        "problems": [{"id": p.id, "title": p.title, "active": p.active, "maximum": problem_maximum(p.id, db)} for p in problems],
        "students": [{"id": u.id, "full_name": u.full_name, "email": u.email, "enrollment_number": u.enrollment_number, "active": u.active} for u in students],
    }


@router.get("/admin/selected")
def admin_selected_results(
    problem_ids: str = Query(...),
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    ids = parse_problem_ids(problem_ids)
    problems = resolve_problems(db, ids)
    students = list(db.scalars(select(User).where(User.role == "student").order_by(User.enrollment_number)))
    student_payloads = [student_result_payload(user, db, ids) for user in students]
    return {
        "problems": [{"id": p.id, "title": p.title, "maximum": problem_maximum(p.id, db)} for p in problems],
        "students": student_payloads,
        "statistics": question_statistics(students, problems, db),
    }


@router.get("/admin/student/{user_id}/selected")
def admin_student_result(
    user_id: int,
    problem_ids: str = Query(...),
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None or user.role != "student":
        raise HTTPException(status_code=404, detail="Student not found.")
    ids = parse_problem_ids(problem_ids)
    return student_result_payload(user, db, ids)


@router.get("/admin/student/{user_id}.pdf")
def admin_student_pdf(
    user_id: int,
    problem_ids: str | None = None,
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    user = db.get(User, user_id)
    if user is None or user.role != "student":
        raise HTTPException(status_code=404, detail="Student not found.")
    ids = parse_problem_ids(problem_ids) if problem_ids else None
    rows, summary = result_rows(user, db, ids)
    return Response(
        create_student_pdf(user, rows, summary),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{user.enrollment_number}_result.pdf"'},
    )


@router.get("/admin/selected.csv")
def admin_selected_csv(
    problem_ids: str = Query(...),
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    ids = parse_problem_ids(problem_ids)
    problems = resolve_problems(db, ids)
    students = list(db.scalars(select(User).where(User.role == "student").order_by(User.enrollment_number)))
    headers, matrix, _ = overall_matrix(students, problems, db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(matrix)
    data = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(io.BytesIO(data), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="selected_results.csv"'})


@router.get("/admin/selected.pdf")
def admin_selected_pdf(
    problem_ids: str = Query(...),
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    ids = parse_problem_ids(problem_ids)
    problems = resolve_problems(db, ids)
    students = list(db.scalars(select(User).where(User.role == "student").order_by(User.enrollment_number)))
    return Response(create_overall_pdf(students, problems, db), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="selected_results.pdf"'})


@router.get("/admin/selected.xlsx")
def admin_selected_xlsx(
    problem_ids: str = Query(...),
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    ids = parse_problem_ids(problem_ids)
    problems = resolve_problems(db, ids)
    students = list(db.scalars(select(User).where(User.role == "student").order_by(User.enrollment_number)))
    return Response(
        create_xlsx(students, problems, db),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="selected_results.xlsx"'},
    )


@router.get("/admin/summary.csv")
def admin_summary_csv(_auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> StreamingResponse:
    problems = list(db.scalars(select(Problem).where(Problem.is_practice.is_(False)).order_by(Problem.id)))
    students = list(db.scalars(select(User).where(User.role == "student").order_by(User.enrollment_number)))
    headers, matrix, _ = overall_matrix(students, problems, db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(matrix)
    data = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(io.BytesIO(data), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="result_summary.csv"'})


@router.post("/admin/email/student")
def email_student_result(
    payload: ResultEmailRequest,
    _auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if payload.user_id is None:
        raise HTTPException(status_code=400, detail="Select a student.")
    user = db.get(User, payload.user_id)
    if user is None or user.role != "student":
        raise HTTPException(status_code=404, detail="Student not found.")
    problems = resolve_problems(db, payload.problem_ids)
    rows, summary = result_rows(user, db, [p.id for p in problems])
    pdf = create_student_pdf(user, rows, summary)
    try:
        send_result_email(to_email=user.email, student_name=user.full_name, rows=rows, summary=summary, pdf_bytes=pdf)
    except MailDeliveryError as exc:
        raise HTTPException(status_code=503, detail="Result email could not be sent. Check SMTP configuration.") from exc
    return {"sent": 1, "failed": 0, "email": user.email}


@router.post("/admin/email/all")
def email_all_results(
    payload: ResultEmailRequest,
    _auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    problems = resolve_problems(db, payload.problem_ids)
    ids = [p.id for p in problems]
    students = list(db.scalars(select(User).where(User.role == "student").order_by(User.enrollment_number)))
    sent = 0
    failed: list[dict] = []
    for user in students:
        rows, summary = result_rows(user, db, ids)
        pdf = create_student_pdf(user, rows, summary)
        try:
            send_result_email(to_email=user.email, student_name=user.full_name, rows=rows, summary=summary, pdf_bytes=pdf)
            sent += 1
        except MailDeliveryError:
            failed.append({"user_id": user.id, "email": user.email})
    return {"sent": sent, "failed": len(failed), "failed_students": failed}

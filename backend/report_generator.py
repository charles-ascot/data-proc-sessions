"""
Report generation pipeline:
1. Query DB for all session data for a given date
2. Send structured prompt to Claude for multi-section analysis
3. Parse AI response into structured JSON
4. Render HTML template with Jinja2
5. Convert HTML to PDF with WeasyPrint
6. Store results in reports table + extract knowledge
"""

import json
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

log = logging.getLogger("report_generator")

TEMPLATE_DIR = Path(__file__).parent / "templates"

ANALYSIS_PROMPT = """You are the CHIMERA Data Analyst. Analyse the following horse racing lay betting session data from {date}.

{rules_description}

SESSION DATA (all sessions for this date):
{session_data}

BETS PLACED:
{bets_data}

RULE EVALUATION RESULTS:
{results_data}

SUMMARY STATISTICS:
{summary_data}

Provide a comprehensive analysis in the following JSON format. Each field should contain 2-4 sentences of specific, data-driven analysis referencing actual numbers from the data:
{{
    "executive_summary": "Overall summary of the day's performance...",
    "odds_drift_patterns": "Analysis of favourite odds distribution and patterns...",
    "rule_distribution": {{
        "analysis": "Which rules triggered most/least and why...",
        "counts": {{"RULE_1": 0, "RULE_2": 0, "RULE_3A": 0, "RULE_3B": 0}}
    }},
    "risk_exposure": "Total liability vs stake analysis, risk ratios, worst-case scenarios...",
    "venue_patterns": "Which venues saw most activity, any venue-specific patterns...",
    "timing_observations": "Session timing patterns, race distribution through the day...",
    "anomalies": "Unusual patterns, outliers, skipped markets, notable events...",
    "suggestions": "Actionable suggestions for rule tuning based on today's data...",
    "win_loss_analysis": "Analysis of outcomes and bet results where data is available...",
    "pnl_estimate": {{
        "total_stake": 0.0,
        "total_liability": 0.0,
        "risk_ratio": 0.0,
        "notes": "Explanation of P&L implications..."
    }},
    "additional_insights": "Any other patterns, correlations, or observations worth noting..."
}}

Return ONLY valid JSON. No markdown formatting, no code blocks."""


async def generate_report(
    pool,
    lay_engine,
    anthropic_client,
    report_id: int,
    report_date: str,
    rules_description: str,
):
    """Full report generation pipeline. Called as a background task."""
    log.info(f"Generating report #{report_id} for {report_date}")

    try:
        # 1. Fetch data from DB
        sessions = await pool.fetch(
            "SELECT raw_json FROM sessions WHERE date = $1", report_date
        )
        bets = await pool.fetch(
            "SELECT raw_json FROM bets WHERE session_id IN "
            "(SELECT session_id FROM sessions WHERE date = $1)",
            report_date,
        )
        results = await pool.fetch(
            "SELECT raw_json FROM results WHERE session_id IN "
            "(SELECT session_id FROM sessions WHERE date = $1)",
            report_date,
        )

        session_data = []
        for s in sessions:
            raw = s["raw_json"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            session_data.append(raw)

        bets_data = []
        for b in bets:
            raw = b["raw_json"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            bets_data.append(raw)

        results_data = []
        for r in results:
            raw = r["raw_json"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            results_data.append(raw)

        # 2. Fetch fresh summary from Lay Engine
        try:
            summary = await lay_engine.get_summary(date=report_date)
        except Exception:
            summary = {
                "total_sessions": len(session_data),
                "total_bets": len(bets_data),
            }

        # 3. Call Claude for analysis
        prompt = ANALYSIS_PROMPT.format(
            date=report_date,
            rules_description=rules_description,
            session_data=json.dumps(session_data, default=str)[:8000],
            bets_data=json.dumps(bets_data, default=str)[:8000],
            results_data=json.dumps(results_data, default=str)[:8000],
            summary_data=json.dumps(summary, default=str),
        )

        message = anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = message.content[0].text.strip()
        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
        analysis = json.loads(raw_text)

        # 4. Render HTML from Jinja2 template
        env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
        template = env.get_template("report.html")
        html_content = template.render(
            date=report_date,
            analysis=analysis,
            sessions=session_data,
            bets=bets_data,
            results=results_data,
            summary=summary,
        )

        # 5. Convert to PDF
        pdf_bytes = HTML(string=html_content).write_pdf()

        # 6. Calculate stats
        total_stake = sum(b.get("size", 0) or 0 for b in bets_data)
        total_liability = sum(b.get("liability", 0) or 0 for b in bets_data)

        # 7. Store in DB
        await pool.execute(
            "UPDATE reports SET status='ready', summary_text=$1, analysis_json=$2, "
            "pdf_bytes=$3, bets_count=$4, total_stake=$5, total_liability=$6, "
            "updated_at=NOW() WHERE id=$7",
            analysis.get("executive_summary", ""),
            json.dumps(analysis),
            pdf_bytes,
            len(bets_data),
            total_stake,
            total_liability,
            report_id,
        )

        # 8. Extract knowledge entries
        knowledge_items = [
            ("odds_trend", analysis.get("odds_drift_patterns", "")),
            ("rule_effectiveness", (analysis.get("rule_distribution") or {}).get("analysis", "")),
            ("risk_analysis", analysis.get("risk_exposure", "")),
            ("venue_pattern", analysis.get("venue_patterns", "")),
            ("suggestion", analysis.get("suggestions", "")),
            ("anomaly", analysis.get("anomalies", "")),
            ("performance", analysis.get("win_loss_analysis", "")),
        ]
        for category, content in knowledge_items:
            if content and len(content) > 10:
                await pool.execute(
                    "INSERT INTO knowledge_base (category, content, source_type, source_id, date_relevant) "
                    "VALUES ($1, $2, 'report', $3, $4)",
                    category,
                    content,
                    str(report_id),
                    report_date,
                )

        log.info(f"Report #{report_id} for {report_date} generated successfully")

    except Exception as e:
        log.error(f"Report generation failed: {e}")
        await pool.execute(
            "UPDATE reports SET status='failed', summary_text=$1, updated_at=NOW() WHERE id=$2",
            f"Generation error: {str(e)}",
            report_id,
        )
        raise

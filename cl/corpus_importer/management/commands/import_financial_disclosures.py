import json
from typing import Dict, Union
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from cl.lib.command_utils import VerboseCommand, logger
from cl.lib.crypto import sha1
from cl.people_db.models import (
    FinancialDisclosure,
    Person,
    Investment,
    Agreements,
    Debt,
    Gift,
    Reimbursement,
    NonInvestmentIncome,
    SpouseIncome,
    Positions,
)
from cl.scrapers.transformer_extractor_utils import get_page_count


def check_if_in_system(sha1_hash: str) -> bool:
    """Check if pdf bytes hash sha1 in cl db.

    :param sha1_hash: Sha1 hash
    :return: Whether PDF is in db.
    """
    disclosures = FinancialDisclosure.objects.filter(pdf_hash=sha1_hash)
    if len(disclosures) > 0:
        logger.info("PDF already in system")
        return True
    return False


def extract_content(pdf_bytes: bytes) -> Dict:
    """Extract the content of the PDF.

    Attempt extraction using multiple methods if necessary.

    :param pdf_bytes: The byte array of the PDF
    :return:The extracted content
    """
    logger.info("Beginning Extraction")

    # Extraction takes between 7 seconds and 80 minutes for super
    # long Trump extraction with ~5k investments
    extractor_response = requests.post(
        settings.BTE_URLS["extract-disclosure"],
        files={"pdf_document": ("file", pdf_bytes)},
        timeout=60 * 120,
    )

    if (
        extractor_response.status_code != 200
        or extractor_response.json()["success"] is False
    ):
        # Try second method
        logger.info("Attempting second extraction")
        extractor_response = requests.post(
            settings.BTE_URLS["extract-disclosure-jw"],
            files={"file": ("file", pdf_bytes)},
            timeout=60 * 60,
        )

        if (
            extractor_response.status_code != 200
            or extractor_response.json()["success"] is False
        ):
            logger.info("Could not extract data from this document")
            return {}

    logger.info("Processing extracted data")
    return extractor_response.json()


def get_report_type(extracted_data: dict) -> int:
    """Get report type if available

    :param extracted_data: Document information
    :return: Disclosure type
    """
    if extracted_data.get("initial"):
        return FinancialDisclosure.INITIAL
    elif extracted_data.get("nomination"):
        return FinancialDisclosure.NOMINATION
    elif extracted_data.get("annual"):
        return FinancialDisclosure.ANNUAL
    elif extracted_data.get("final"):
        return FinancialDisclosure.FINAL


def save_disclosure(
    extracted_data: dict, disclosure: FinancialDisclosure
) -> None:
    """Save financial data to system.

    Wrapped in a transaction, we fail if anything fails.

    :param disclosure: Financial disclosure
    :param extracted_data: disclosure
    :return:None
    """
    addendum = "Additional Information or Explanations"

    # Process and save our data into the system.
    with transaction.atomic():
        disclosure.has_been_extracted = True
        disclosure.addendum_content_raw = extracted_data[addendum]["text"]
        disclosure.addendum_redacted = extracted_data[addendum]["is_redacted"]
        disclosure.is_amended = extracted_data.get("amended") or False
        disclosure.report_type = get_report_type(extracted_data)
        disclosure.save()

        for investment in extracted_data["sections"]["Investments and Trusts"][
            "rows"
        ]:
            Investment.objects.create(
                financial_disclosure=disclosure,
                redacted=True
                in [v["is_redacted"] for _, v in investment.items()],
                description=investment["A"]["text"],
                has_inferred_values=investment["A"]["inferred_value"],
                income_during_reporting_period_code=investment["B1"]["text"],
                income_during_reporting_period_type=investment["B2"]["text"],
                gross_value_code=investment["C1"]["text"],
                gross_value_method=investment["C2"]["text"],
                transaction_during_reporting_period=investment["D1"]["text"],
                transaction_date_raw=investment["D2"]["text"],
                transaction_value_code=investment["D3"]["text"],
                transaction_gain_code=investment["D4"]["text"],
                transaction_partner=investment["D5"]["text"],
            )

        for agreement in extracted_data["sections"]["Agreements"]["rows"]:
            Agreements.objects.create(
                financial_disclosure=disclosure,
                redacted=True
                in [v["is_redacted"] for _, v in agreement.items()],
                date=agreement["Date"]["text"],
                parties_and_terms=agreement["Parties and Terms"]["text"],
            )

        for debt in extracted_data["sections"]["Liabilities"]["rows"]:
            Debt.objects.create(
                financial_disclosure=disclosure,
                redacted=True in [v["is_redacted"] for _, v in debt.items()],
                creditor_name=debt["Creditor"]["text"],
                description=debt["Description"]["text"],
                value_code=debt["Value Code"]["text"],
            )

        for position in extracted_data["sections"]["Positions"]["rows"]:
            Positions.objects.create(
                financial_disclosure=disclosure,
                redacted=True
                in [v["is_redacted"] for _, v in position.items()],
                position=position["Position"]["text"].replace("LL. ", ""),
                organization_name=position["Name of Organization"]["text"],
            )

        for gift in extracted_data["sections"]["Gifts"]["rows"]:
            Gift.objects.create(
                financial_disclosure=disclosure,
                source=gift["Source"]["text"],
                description=gift["Description"]["text"],
                value_code=gift["Value"]["text"],
                redacted=True in [v["is_redacted"] for _, v in gift.items()],
            )

        for reimbursement in extracted_data["sections"]["Reimbursements"][
            "rows"
        ]:
            if 5 != len(reimbursement.items()):
                # Just in case - probably not needed
                continue
            Reimbursement.objects.create(
                financial_disclosure=disclosure,
                redacted=True
                in [v["is_redacted"] for _, v in reimbursement.items()],
                source=reimbursement["Source"]["text"],
                dates=reimbursement["Dates"]["text"],
                location=reimbursement["Locations"]["text"],
                purpose=reimbursement["Purpose"]["text"],
                items_paid_or_provided=reimbursement["Items Paid or Provided"][
                    "text"
                ],
            )

        for non_investment_income in extracted_data["sections"][
            "Non-Investment Income"
        ]["rows"]:
            NonInvestmentIncome.objects.create(
                financial_disclosure=disclosure,
                redacted=True
                in [
                    v["is_redacted"] for _, v in non_investment_income.items()
                ],
                date=non_investment_income["Date"]["text"],
                source_type=non_investment_income["Source and Type"]["text"],
                income_amount=non_investment_income["Income"]["text"],
            )

        for spouse_income in extracted_data["sections"][
            "Non Investment Income Spouse"
        ]["rows"]:
            SpouseIncome.objects.create(
                financial_disclosure=disclosure,
                redacted=True
                in [v["is_redacted"] for _, v in spouse_income.items()],
                date=spouse_income["Date"]["text"],
                source_type=spouse_income["Source and Type"]["text"],
            )


def generate_or_download_disclosure_as_pdf(
    data: Dict[str : Union[str, int, list]]
) -> requests.Response:
    """Generate or download PDF content from images or urls.

    :param data: Data to process.
    :return: Response containing PDF
    """
    if data["disclosure_type"] == "jw":
        # Download the PDFs in the judicial watch collection
        logger.info(
            f"Preparing to process JW url: {quote(data['url'], safe=':/')}"
        )
        pdf_response = requests.get(data["url"], timeout=60 * 20)

    elif data["disclosure_type"] == "single":
        # Split single long tiff into multiple tiffs and combine into PDF
        logger.info(
            f"Preparing to process url: {quote(data['url'], safe=':/')}"
        )
        pdf_response = requests.post(
            settings.BTE_URLS["image-to-pdf"],
            params={"tiff_url": data["url"]},
            timeout=10 * 60,
        )
    else:
        # Combine split tiffs into one single PDF
        logger.info(
            f"Preparing to process split urls: "
            f"{quote(data['urls'][0], safe=':/')}"
        )
        pdf_response = requests.post(
            settings.BTE_URLS["urls-to-pdf"],
            json=json.dumps({"urls": data["urls"]}),
        )
    return pdf_response


def import_financial_disclosures(options):
    """Import financial documents into www.courtlistener.com

    :param options: argparse
    :return:None
    """
    filepath = options["filepath"]
    with open(filepath) as f:
        disclosures = json.load(f)

    for data in disclosures:
        # Generate PDF content from our three paths
        if options["skip_until"]:
            if data["id"] < int(options["skip_until"]):
                continue

        year = data["year"]
        person_id = data["person_id"]
        logger.info(
            f"Processing id:{person_id} " f"year:{year}, with id:{data['id']}"
        )

        pdf_response = generate_or_download_disclosure_as_pdf(data)
        pdf_bytes = pdf_response.content

        if pdf_response.status_code != 200:
            logger.info("PDF generation failed.")
            continue

        logger.info("PDF content generated. Extracting content now.")

        # Sha1 hash - Check for duplicates
        sha1_hash = sha1(pdf_bytes)
        in_system = check_if_in_system(sha1_hash)
        if in_system:
            logger.info("\x1b[6;30;41m" + "PDF already in system." + "\x1b[0m")
            continue

        # Return page count - 0 indicates a failure of some kind.  Like PDF
        # Not actually present on aws.
        pg_count = get_page_count(pdf_bytes)
        if pg_count == 0:
            logger.info("\x1b[6;30;41m" + "PDF failed!" + "\x1b[0m")
            return

        # Save Financial Disclosure here to AWS and move onward
        disclosure = FinancialDisclosure(
            year=year,
            page_count=pg_count,
            person=Person.objects.get(id=person_id),
            pdf_hash=sha1_hash,
            has_been_extracted=False,
        )
        # Save and upload PDF
        disclosure.filepath.save("", ContentFile(pdf_bytes))
        logger.info(
            f"Uploaded to https://{settings.AWS_S3_CUSTOM_DOMAIN}/"
            f"{disclosure.filepath}"
        )
        # Extract content from PDF
        content = extract_content(pdf_bytes=pdf_bytes)
        if not content:
            logger.info("\x1b[6;30;41m" + "Failed extraction!" + "\x1b[0m")

        # Save PDF content
        save_disclosure(extracted_data=content, disclosure=disclosure)


class Command(VerboseCommand):
    help = "Add financial disclosures to CL database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--filepath",
            required=True,
            help="Filepath to json identify documents to process.",
        )

        parser.add_argument(
            "--skip-until",
            required=False,
            help="Skip until, uses an id to skip processes",
        )

    def handle(self, *args, **options):
        super(Command, self).handle(*args, **options)
        import_financial_disclosures(options)
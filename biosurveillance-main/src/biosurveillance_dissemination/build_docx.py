import sys
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.shared import Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from doc_styles import create_styles
from docx.oxml import register_element_cls

from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[4]))
from xml_utils import set_paragraph_bottom_border, CT_Anchor, add_float_picture

class BiosurveillanceReport:
    def __init__(self, quarter, year, saveDir):
        self.report_quarter = quarter 
        self.report_year = year
        self.saveDir = saveDir
        self.doc = self.reportSetup()

    def reportSetup(self):
        register_element_cls('wp:anchor', CT_Anchor)
        doc = Document()
        create_styles(doc)

        doc.core_properties.title = 'Non-fatal Overdose Biosurveillance Report; ' + self.report_year + " Quarter " + self.report_quarter 
        doc.core_properties.subject = 'Summary of toxicological analysis of residual samples from non-fatal overdoses in Q' + self.report_quarter + ' ' + str(self.report_year)
        doc.core_properties.author = "Wisconsin Department of Health Services, Office of Health Informatics"
        doc.core_properties.language = 'English'

        return doc
    
class ReportPage:
    def __init__(self, report, imgs):
        self.report = report
        self.imgs = imgs
        self.pageSetup()
        
    def pageSetup(self):
        section = self.report.doc.sections[-1]
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

        # -------------------------
        # Page Title 
        # -------------------------
        title1 = self.report.doc.add_heading("NON-FATAL OVERDOSE\nBIOSURVEILLANCE", level=1)
        title1.alignment = WD_ALIGN_PARAGRAPH.LEFT

        # -------------------------
        # Page Sub-heading
        # -------------------------
        subtitle1 = self.report.doc.add_heading(
            self.report.report_year + " QUARTER " + self.report.report_quarter,
            level=2
            )
        subtitle1.alignment = WD_ALIGN_PARAGRAPH.LEFT
        set_paragraph_bottom_border(subtitle1, thickness_pt=2, color_hex='002B56') 
        
        # -------------------------
        # Add [image] and caption
        # -------------------------
        fig1_title = "This is a placeholder title."
        fig2_title = "This is a second placeholder title."
        fig3_title = "This is a third placeholder title."
        paragraph = self.report.doc.add_paragraph()
        paragraph.style = 'figure_caption'
        add_float_picture(paragraph, self.imgs[1], width=Inches(2), pos_x=Inches(0.75), pos_y=Inches(2.15), wrap='wrapSquare', title=fig1_title, descr="This is a temporary placeholder title to populate the 'description' parameter of the add_picture function.")
        add_float_picture(paragraph, self.imgs[2], width=Inches(2), pos_x=Inches(3), pos_y=Inches(2.15), wrap='wrapSquare', title=fig2_title, descr="This is a second temporary placeholder title to populate the 'description' parameter of the add_picture function.")

        paragraph.add_run("\n".join(["Here is some text." for i in range(8)]))

        sectionHeader1 = self.report.doc.add_heading(
            "5 MOST COMMONLY DETECTED SUBSTANCES" + "\n".join(["" for i in range(10)]),
            level=3
        )
        sectionHeader1.alignment = WD_ALIGN_PARAGRAPH.LEFT

        add_float_picture(paragraph, self.imgs[3], width=Inches(4.5), pos_x=Inches(0.75), pos_y=Inches(4.75), wrap='wrapNone', title=fig3_title, descr="This is a third temporary placeholder title to populate the 'description' parameter of the add_picture function.")
        
        sectionHeader2 = self.report.doc.add_heading(
            "NOVEL SUBSTANCES",
            level=3
        )
        sectionHeader2.alignment = WD_ALIGN_PARAGRAPH.LEFT
        add_float_picture(paragraph, self.imgs[3], width=Inches(3), pos_x=Inches(0.75), pos_y=Inches(7.75), wrap='wrapNone', title=fig3_title, descr="This is a third temporary placeholder title to populate the 'description' parameter of the add_picture function.")
        add_float_picture(paragraph, self.imgs[0], width=Inches(3), pos_x=Inches(0.5), pos_y=Inches(10), wrap='wrapSquare', title=fig1_title, descr="This is a temporary placeholder title to populate the 'description' parameter of the add_picture function.")

        # self.report.doc.add_paragraph(fig1_title, style='figure_caption')
        
def create_reports():
    saveDir = "L:\\Bhip_Rms_Shared\\Research Unit\\OD2A analyst\\projects\\cdc_submissions\\biosurveillance_analytics\\docs\\"
    report = BiosurveillanceReport('1', '2026', saveDir)
    imgs = ["L:\\Bhip_Rms_Shared\\Research Unit\\OD2A analyst\\projects\\cdc_submissions\\biosurveillance_analytics\\docs\\dhs-logo-text-color.png",
            "L:\\Bhip_Rms_Shared\\Research Unit\\OD2A analyst\\projects\\cdc_submissions\\biosurveillance_analytics\\docs\\testImage1.png",
            "L:\\Bhip_Rms_Shared\\Research Unit\\OD2A analyst\\projects\\cdc_submissions\\biosurveillance_analytics\\docs\\testImage2.png",
            "L:\\Bhip_Rms_Shared\\Research Unit\\OD2A analyst\\projects\\cdc_submissions\\biosurveillance_analytics\\docs\\testImage3.png"]
    ReportPage(report, imgs)
    report.doc.save(saveDir + "test.docx")



    #     # -------------------------
    #     # Format container table
    #     # -------------------------
    #     table = self.doc.add_table(rows=3, cols=2)
    #     table.style = "Table Grid"
    #     for cell in table._cells:
    #         cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    #         cell.alignment = WD_TABLE_ALIGNMENT.CENTER
    #         set_cell_margins(cell, top=Inches(0.0), bottom=Inches(0.0), left=Inches(0.2), right=Inches(0.2),)
    #         set_cell_borders(cell, border_names=['top', 'left', 'bottom',  'right', 'insideH', 'insideV'], border_style='nil')
        
    #     set_cell_borders(table.cell(1,0), border_names=['bottom'], border_style='single', border_width=8*2, border_color=dhs_colors['navy'][1:])
    #     set_cell_borders(table.cell(1,1), border_names=['bottom'], border_style='single', border_width=8*2, border_color=dhs_colors['navy'][1:])
    #     table.cell(0,0).merge(table.cell(0,1))

    #     # -------------------------
    #     # Add & format disclaimer header
    #     # -------------------------
    #     cell = table.cell(0,0)
    #     paragraph = cell.paragraphs[0]
    #     paragraph.style = 'Subtitle1'
    #     run = paragraph.add_run()
    #     run.add_text("The following data are based on suspected* opioid overdose cases in Wisconsin as determined by Wisconsin ambulance run reports.")
    #     cell.add_paragraph()
    #     paragraph = cell.paragraphs[1]
    #     paragraph.style = 'Subtitle2'
    #     run = paragraph.add_run()
    #     run.add_text("These data are provisional and subject to change.")

    #     # -------------------------
    #     # Add & format data table
    #     # -------------------------
    #     cell = table.cell(1,0)
    #     self.data_table = cell.add_table(rows=11, cols=4)
    #     self.fmt_dt()

    #     # -------------------------
    #     # Add tornado charte and title
    #     # -------------------------
    #     fig1_title = "Figure 1. " + self.data.label + " Suspected Opioid Overdose by Age and Gender, " + self.report_monthName + ' ' + str(self.report_year)
    #     cell.add_paragraph(fig1_title, style='figure_title')
        
    #     run = cell.add_paragraph().add_run()
    #     run.add_picture(self.fig1, width=Inches(5), title=fig1_title, descr="Rates of opioid overdose for " + self.data.label + " in " + self.report_monthName + " " + str(self.report_year) + ', grouped by age group and sex.')

    #     #Add source notes
    #     paragraph = cell.add_paragraph()
    #     paragraph.style = 'note'
    #     run = paragraph.add_run()
    #     run.add_text("Source: Office of Health Informatics, Wisconsin Department of Health Services")
    #     run.add_break()
    #     run.add_text("Data: Wisconsin Ambulance Run Data System (WARDS)")

    #     # -------------------------
    #     # Add bar chart and title
    #     # -------------------------
    #     fig2_title = "Figure 2. " + self.data.label + " Suspected Opioid Overdose by Month, YTD"
    #     cell = table.cell(1,1)
    #     set_cell_margins(cell, left=Inches(0.2), right=Inches(0.2))
    #     paragraph = cell.paragraphs[0]
    #     paragraph.style = 'figure_title'
    #     run = paragraph.add_run()
    #     run.add_text(fig2_title)

    #     #Add bar chart
    #     cell.add_paragraph()
    #     paragraph = cell.paragraphs[1]
    #     run = paragraph.add_run()
    #     run.add_picture(self.fig2, width=Inches(5), title=fig2_title, descr= "Cumulative monthly overdose counts for " + self.data.label + " since " + str(int(min(self.data.geo_ems_data['year']))) + ".")

    #     #Add source notes
    #     cell.add_paragraph()
    #     paragraph = cell.paragraphs[2]
    #     paragraph.style = 'note'
    #     run = paragraph.add_run()
    #     run.add_text("Source: Office of Health Informatics, Wisconsin Department of Health Services")
    #     run.add_break()
    #     run.add_text("Data: Wisconsin Ambulance Run Data System (WARDS)")
    #     run.add_break()
    #     run.add_break()
    #     run.add_break()
        
    #     #Add note
    #     cell.add_paragraph()
    #     paragraph = cell.paragraphs[3]
    #     paragraph.style = 'note2'
    #     run = paragraph.add_run()
    #     run.add_text("*These data include all ambulance runs within Wisconsin coded as 911 responses for individuals aged 11 years and older; medical transports and other non-emergency transports have been excluded. Data are determined from free-text fields via key words of interest and exclude visits that appear to be related to withdrawal, detox, or intentional overdose. These cases have not been confirmed by clinicians.")

    #     # -------------------------
    #     # Add DHS logo and resource links
    #     # -------------------------
    #     cell = table.cell(2,0)
    #     # set_cell_margins(cell, top=Inches(0), bottom=Inches(0))
    #     paragraph = cell.paragraphs[0]
    #     run = paragraph.add_run()
    #     run.add_picture(dhs_logo, width=section.page_width/3.5, title='DHS logo', descr= "The Wisconsin Department of Health Services logo")
        
    #     #Add resources
    #     cell = table.cell(2,1)
    #     for parag in range(4):
    #         cell.paragraphs[parag].style = 'note2'
    #         cell.add_paragraph()
    #     cell.paragraphs[0].text = ""
    #     cell.paragraphs[1].text = "For additional Wisconsin opioid resources: "
    #     add_hyperlink(cell.paragraphs[1], "www.dhs.wisconsin.gov/opioids", "www.dhs.wisconsin.gov/opioids", None, True)    
    #     cell.paragraphs[2].text = "For additional data requests, email: "
    #     add_hyperlink(cell.paragraphs[2], "dhshealthstats@wisconsin.gov ", "dhshealthstats@wisconsin.gov ", None, True)
    #     cell.paragraphs[3].text = self.reportNumb + " (" + str(self.report_month) + "/" + str(self.report_year) + ")"
    #     cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    
    # def fmt_dt(self):
    #     """Creates the structure/bones for the data table. Includes formatting for all elements except for actual data."""
    #     self.data_table.style = "Table Grid"
    #     self.data_table.allow_autofit = False
    #     for row in self.data_table.rows:
    #         row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    #         row.height = Inches(0.24)
    #         for cell in row.cells:
    #             cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    #     self.data_table.rows[-1].height = Inches(0.18)
    #     for row in self.data_table.rows:
    #         for cell in row.cells[1:]:
    #             cell.width = Inches(2.78/3)

    #     set_cell_background(self.data_table.cell(0,0), dhs_colors['black'][1:])
    #     set_cell_background(self.data_table.cell(2,0), dhs_colors['tableBlue'][1:])
    #     set_cell_background(self.data_table.cell(5,0), dhs_colors['tableBlue'][1:])

    #     for cell in self.data_table.rows[1].cells:
    #         set_cell_background(cell, dhs_colors['navy'][1:])
        
    #     #Add table note
    #     paragraph = self.data_table.cell(10,0).paragraphs[0]
    #     set_cell_borders(self.data_table.cell(10,0), border_names=['left', 'bottom', 'right'], border_style='nil',)
    #     paragraph.style = 'note'
    #     run = paragraph.add_run()
    #     run.add_text("*The most recent month available")
    #     self.pop_dt()
    
    # def pop_dt(self):
    #     merge_cols = len(self.tableData[0]) - 1
    #     for row_ix, row in enumerate(self.tableData):
    #         self.data_table.cell(row_ix, merge_cols).merge(self.data_table.cell(row_ix, 3))
    #         for col_ix, col in enumerate(row):
    #             self.data_table.cell(row_ix, col_ix).text = col
    #             self.data_table.cell(row_ix, col_ix).style = 'table_data'
    #     for ix in [0, 2, 5, 10]:
    #         self.data_table.rows[ix].cells[0].merge(self.data_table.rows[ix].cells[-1])
        
    #     self.data_table.cell(0,0).paragraphs[0].style = "table_header1"
    #     for cell in self.data_table.rows[1].cells[1:]:
    #         cell.paragraphs[0].style = "table_header1"
    #     self.data_table.cell(2,0).paragraphs[0].style = "table_header2"
    #     for ix in [3, 4, 6, 7, 8, 9]:
    #         self.data_table.rows[ix].cells[0].width = Inches(2.22)
    #         self.data_table.rows[ix].cells[0].paragraphs[0].style = 'table_measure'
    #         for cell in self.data_table.rows[ix].cells[1:]:
    #             cell.width = Inches(float(2.78/merge_cols))
    #             cell.paragraphs[0].style = 'table_data'
    #     self.data_table.cell(5,0).paragraphs[0].style = "table_header2"
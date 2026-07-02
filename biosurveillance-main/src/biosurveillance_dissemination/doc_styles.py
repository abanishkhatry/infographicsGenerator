from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_LINE_SPACING, WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

class ParagraphStyle:
    def __init__(self, styles, name, parent, **kwargs):
        self._custom_style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        self._custom_style.base_style = parent
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def name(self):
        return self._name
    
    @name.setter
    def name(self, data):
        self._name = data
        
    @property
    def parent(self):
        return self._parent
    
    @parent.setter
    def parent(self, data):
        self._parent = data
    
    @property
    def fontName(self):
        return self._fontName
    
    @fontName.setter
    def fontName(self, data):
        self._fontName = data
        self._custom_style.font.name = self._fontName
        
    @property
    def bold(self):
        return self._bold
    
    @bold.setter
    def bold(self, data):
        self._bold = data
        self._custom_style.font.bold = self._bold
        
    @property
    def italic(self):
        return self._italic
    
    @italic.setter
    def italic(self, data):
        self._italic = data
        self._custom_style.font.italic = self._italic
        
    @property
    def fontSize(self):
        return self._fontSize
    
    @fontSize.setter
    def fontSize(self, data):
        self._fontSize = data
        self._custom_style.font.size = Pt(self._fontSize)
        
    @property
    def textColor(self):
        return self._textColor
    
    @textColor.setter
    def textColor(self, data):
        self._textColor = data
        self._custom_style.font.color.rgb = RGBColor.from_string(self._textColor)
        
    @property
    def leading(self):
        return self._leading
    
    @leading.setter
    def leading(self, data):
        self._leading = data
        self._custom_style.paragraph_format.space_before = Pt(0)
        self._custom_style.paragraph_format.space_after = Pt(0)
        self._custom_style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
        self._custom_style.paragraph_format.line_spacing = Pt(self._leading)
        
    @property
    def alignment(self):
        return self._alignment
    
    @alignment.setter
    def alignment(self, data):
        self._alignment = data
        self._custom_style.paragraph_format.alignment = self._alignment

def create_styles(doc):
    styles = doc.styles

    #Header 1 - from DHS template
    styles['Heading 1'].font.name = "Verdana"
    styles['Heading 1'].font.size = Pt(20)
    styles['Heading 1'].font.bold = True
    styles['Heading 1'].font.color.rgb = RGBColor.from_string('003D78')
    styles['Heading 1'].paragraph_format.space_before = Pt(0)
    styles['Heading 1'].paragraph_format.space_after = Pt(6)
    styles['Heading 1'].paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    styles['Heading 1'].paragraph_format.line_spacing = 1.15

    #Header 2 - from DHS template
    styles['Heading 2'].font.name = "Verdana"
    styles['Heading 2'].font.size = Pt(16)
    styles['Heading 2'].font.bold = True
    styles['Heading 2'].font.color.rgb = RGBColor.from_string('002B56')
    styles['Heading 2'].paragraph_format.space_before = Pt(0)
    styles['Heading 2'].paragraph_format.space_after = Pt(3)
    styles['Heading 2'].paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    styles['Heading 2'].paragraph_format.line_spacing = 1.15

    #Header 2 - from DHS template
    styles['Heading 3'].font.name = "Verdana"
    styles['Heading 3'].font.size = Pt(14)
    styles['Heading 3'].font.bold = True
    styles['Heading 3'].font.color.rgb = RGBColor.from_string('285887')
    styles['Heading 3'].paragraph_format.space_before = Pt(12)
    styles['Heading 3'].paragraph_format.space_after = Pt(0)
    styles['Heading 3'].paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    styles['Heading 3'].paragraph_format.line_spacing = 1.15

    rFonts = styles['Heading 1'].element.get_or_add_rPr().get_or_add_rFonts()
    rFonts.set(qn("w:asciiTheme"), 'Verdana')
    rFonts = styles['Heading 2'].element.get_or_add_rPr().get_or_add_rFonts()
    rFonts.set(qn("w:asciiTheme"), 'Verdana')
    rFonts = styles['Heading 3'].element.get_or_add_rPr().get_or_add_rFonts()
    rFonts.set(qn("w:asciiTheme"), 'Verdana')

    ParagraphStyle(
        styles,
        name='figure_caption',
        fontName='Tahoma',
        parent=styles['Normal'],
        fontSize=14,
        alignment=WD_ALIGN_PARAGRAPH.LEFT,)
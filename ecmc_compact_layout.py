"""Compact responsive card grid, shared by hardware and multi-axis panels."""
def column_count(width, count, preferred, gap=6):
    if count<=0:
        return 1
    return max(1,min(count,(max(0,width)+gap)//(max(1,preferred)+gap)))

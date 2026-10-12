import { ref, onUnmounted } from 'vue'

export function useTableDrag() {
  // 鼠标抓手拖拽滑动 (Drag-to-Scroll)
  const tableWrapperRef = ref(null)
  const isDragging = ref(false)
  let startY = 0
  let startScrollTop = 0
  let hasMoved = false

  function onTableMouseDown(e) {
    if (e.target.closest('button') || e.target.closest('input') || e.target.closest('select') || e.target.closest('a')) {
      return
    }
    if (e.button !== 0) return

    isDragging.value = true
    hasMoved = false
    startY = e.clientY

    const scroller = document.querySelector('.content-area') || window
    startScrollTop = scroller.scrollTop !== undefined ? scroller.scrollTop : window.scrollY

    window.addEventListener('mousemove', onTableMouseMove)
    window.addEventListener('mouseup', onWindowMouseUp)
  }

  function onTableMouseMove(e) {
    if (!isDragging.value) return
    const deltaY = e.clientY - startY
    if (Math.abs(deltaY) > 4) {
      hasMoved = true
    }
    const scroller = document.querySelector('.content-area') || window
    if (scroller.scrollTop !== undefined) {
      scroller.scrollTop = startScrollTop - deltaY
    } else {
      window.scrollTo(0, startScrollTop - deltaY)
    }
  }

  function onWindowMouseUp() {
    if (isDragging.value) {
      isDragging.value = false
      setTimeout(() => {
        hasMoved = false
      }, 50)
    }
    window.removeEventListener('mousemove', onTableMouseMove)
    window.removeEventListener('mouseup', onWindowMouseUp)
  }

  onUnmounted(() => {
    window.removeEventListener('mousemove', onTableMouseMove)
    window.removeEventListener('mouseup', onWindowMouseUp)
  })

  return { tableWrapperRef, isDragging, onTableMouseDown, onTableMouseMove, onWindowMouseUp, wasDragged: () => hasMoved }
}

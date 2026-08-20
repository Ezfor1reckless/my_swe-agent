import curses 
import random 
 
def main(stdscr): 
    curses.curs_set(0) # Hide cursor 
    stdscr.nodelay(1)  # Non-blocking input 
    stdscr.timeout(100) # Refresh every 100 ms 
 
    sh, sw = stdscr.getmaxyx() # Screen height and width 
 
    snake = [[sh // 2, sw // 2 + 1], [sh // 2, sw // 2], [sh // 2, sw // 2 - 1]] 
    direction = curses.KEY_RIGHT 
 
    food = [sh // 2, sw // 4] 
 
    score = 0 
 
    while True: 
        next_key = stdscr.getch() 
        if next_key != -1: 
            if next_key == curses.KEY_UP and direction != curses.KEY_DOWN: 
                direction = curses.KEY_UP 
            elif next_key == curses.KEY_DOWN and direction != curses.KEY_UP: 
                direction = curses.KEY_DOWN 
            elif next_key == curses.KEY_LEFT and direction != curses.KEY_RIGHT: 
                direction = curses.KEY_LEFT 
            elif next_key == curses.KEY_RIGHT and direction != curses.KEY_LEFT: 
                direction = curses.KEY_RIGHT 
 
        head = snake[0][:] 
        if direction == curses.KEY_UP: 
            head[0] -= 1 
        elif direction == curses.KEY_DOWN: 
            head[0] += 1 
        elif direction == curses.KEY_LEFT: 
            head[1] -= 1 
        elif direction == curses.KEY_RIGHT: 
            head[1] += 1 
 
        snake.insert(0, head) 
 
                head in snake[1:]): 
            break 
 
        if head == food: 
            score += 1 
            while food in snake: 
                food = [random.randint(0, sh - 1), random.randint(0, sw - 1)] 
            stdscr.addch(food[0], food[1], curses.ACS_PI) 
        else: 
            tail = snake.pop() 
            stdscr.addch(tail[0], tail[1], ' ') 
 
        stdscr.clear() 
        for y, x in snake: 
            stdscr.addch(y, x, curses.ACS_CKBOARD) 
        stdscr.addch(food[0], food[1], curses.ACS_PI) 
 
        stdscr.addstr(0, 0, "Score: " + str(score)) 
 
        stdscr.refresh() 
 
    stdscr.addstr(sh // 2, sw // 2 - len("Game Over!") // 2, "Game Over!") 
    stdscr.addstr(sh // 2 + 1, sw // 2 - len("Score: " + str(score)) // 2, "Score: " + str(score)) 
    stdscr.nodelay(0) 
    stdscr.getch() 
 
if __name__ == '__main__': 
    curses.wrapper(main) 

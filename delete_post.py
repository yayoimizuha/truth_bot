import pickle
import sys


def remove_value_from_set_in_pickle(filepath, value_to_remove):
    """
    指定されたpickleファイルに保存されているsetから、指定された値を削除し、ファイルを更新します。

    Args:
        filepath (str): pickleファイルのパス。
        value_to_remove (int): 削除する値。
    """

    # pickleファイルからsetを読み込む
    with open(filepath, 'rb') as f:
        data_set:set[int] = pickle.load(f)

    # 値を削除する
    print(data_set)
    data_set.remove(value_to_remove)  # discardは値が存在しなくてもエラーにならない

    # 更新されたsetをpickleファイルに保存する
    with open(filepath, 'wb') as f:
        pickle.dump(data_set, f)


# 使用例
if __name__ == '__main__':
    filepath = 'proceed.pickle'
    value_to_remove = sys.argv[-1]

    remove_value_from_set_in_pickle(filepath, value_to_remove)

    print(f"{filepath}から{value_to_remove}を削除しました。")
